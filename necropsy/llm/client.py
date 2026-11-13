"""Provider transport behind one protocol (design section 4.5, R-15.7, E9).

Three rules live here rather than in the caller:

  - **One call, one object.** `complete_json` asks for every narrative field in a
    single response. A second call for "just the epitaph" is a defect (E9), so
    there is no method that could make one.
  - **A provider is optional.** `build_client` returns `None` when no key is
    configured, which is the signal the narrator reads to use deterministic
    templates. NECROPSY with no LLM key still produces all seven sections (R-13.8),
    so "no provider" is a supported configuration, not an error path.
  - **Egress is one destination.** The only URL this module builds is
    `{LLM_BASE_URL}/chat/completions`. GitHub plus the configured LLM endpoint is
    the complete list of destinations NECROPSY talks to (E4).

`FakeLlmClient` is the test double: the suite runs with no key and no network
(E11). `RaisingLlmClient` is the stronger double used to prove Property 11 — that
no cache-hit path can reach a provider — by failing loudly if it is touched at all.

The key is read from the environment, is never logged, and never appears in a
response, a repr, or an error detail (E12).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from necropsy.config import Config, load_config
from necropsy.llm.prompt import Prompt

log = logging.getLogger("necropsy.llm.client")

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 20.0

#: Enough for the whole narrative object and no more. A model that needs more
#: than this is not answering the question it was asked.
MAX_OUTPUT_TOKENS = 1_400

#: Deterministic output is a product requirement, not a preference: the same
#: repository at the same SHA should read the same way if it is ever re-analyzed.
TEMPERATURE = 0.2


class LlmError(Exception):
    """A provider failure. Carries no key material and no prompt text (E12).

    Raised for transport errors, non-2xx responses and malformed envelopes.
    It is never raised for *content* problems: unparseable or ungrounded output
    is the validator's business, and the validator has fallbacks for it (E10).
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class LlmResult:
    """One completed call, plus the accounting `llm_calls` persists (R-15.4)."""

    text: str
    model: str
    latency_ms: int
    tokens_in: int | None = None
    tokens_out: int | None = None

    @property
    def chars(self) -> int:
        return len(self.text)


@runtime_checkable
class LlmClient(Protocol):
    """The only interface the pipeline knows. One method, one call, one object."""

    model: str

    def complete_json(self, prompt: Prompt) -> LlmResult:
        """Return the model's raw text. Parsing and trusting it is not this layer's job."""
        ...


# --------------------------------------------------------------------------
# the real provider
# --------------------------------------------------------------------------
class OpenAiCompatibleClient:
    """Chat-completions transport for any OpenAI-compatible endpoint.

    One shape covers OpenAI, Groq, Together, OpenRouter and a local server, which
    is why `LLM_PROVIDER` selects a base URL rather than a different code path.
    `response_format=json_object` is requested when available; the validator
    never assumes it was honored.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        http: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._base_url = base_url.rstrip("/")
        self._owns_http = http is None
        self._http = http or httpx.Client(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
                write=READ_TIMEOUT_SECONDS,
                pool=CONNECT_TIMEOUT_SECONDS,
            ),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": "necropsy/1.0",
            },
        )

    def __repr__(self) -> str:  # pragma: no cover - security-relevant, not logic
        return f"OpenAiCompatibleClient(model={self.model!r}, base_url={self._base_url!r})"

    __str__ = __repr__

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def complete_json(self, prompt: Prompt) -> LlmResult:
        body: dict[str, Any] = {
            "model": self.model,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        started = time.monotonic()
        try:
            response = self._http.post(f"{self._base_url}/chat/completions", json=body)
        except httpx.TransportError as exc:
            raise LlmError(
                f"The LLM provider could not be reached ({type(exc).__name__})."
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        if response.status_code >= 400:
            # The provider's body may echo request content; only the status is kept.
            raise LlmError(
                f"The LLM provider returned HTTP {response.status_code}.",
                status=response.status_code,
            )

        try:
            payload = response.json()
            choices = payload["choices"]
            text = choices[0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmError("The LLM provider returned an unrecognized envelope.") from exc

        if not isinstance(text, str) or not text.strip():
            raise LlmError("The LLM provider returned an empty completion.")

        usage = payload.get("usage") if isinstance(payload, dict) else None
        tokens_in = tokens_out = None
        if isinstance(usage, dict):
            tokens_in = _as_int(usage.get("prompt_tokens"))
            tokens_out = _as_int(usage.get("completion_tokens"))

        return LlmResult(
            text=text,
            model=str(payload.get("model") or self.model),
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        )


def _as_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


# --------------------------------------------------------------------------
# test doubles
# --------------------------------------------------------------------------
@dataclass
class FakeLlmClient:
    """A scripted provider. The suite needs no key and no network (E11, R-15.7).

    `responses` is consumed in order. A string is returned as the completion; an
    exception instance is raised. Running out of scripted responses raises, which
    is deliberate: a test that triggers more calls than it scripted has found a
    defect in call ordering, and silence would hide it.
    """

    responses: Sequence[str | BaseException] = field(default_factory=tuple)
    model: str = "fake-model-1"
    latency_ms: int = 7
    prompts: list[Prompt] = field(default_factory=list)
    calls: int = 0

    def complete_json(self, prompt: Prompt) -> LlmResult:
        self.prompts.append(prompt)
        index = self.calls
        self.calls += 1
        if index >= len(self.responses):
            raise AssertionError(
                f"FakeLlmClient was called {self.calls} time(s) but only "
                f"{len(self.responses)} response(s) were scripted"
            )
        scripted = self.responses[index]
        if isinstance(scripted, BaseException):
            raise scripted
        return LlmResult(
            text=scripted,
            model=self.model,
            latency_ms=self.latency_ms,
            tokens_in=512,
            tokens_out=256,
        )

    # -- convenience for tests ---------------------------------------------
    @classmethod
    def returning(cls, *payloads: dict[str, Any] | str) -> FakeLlmClient:
        """Script one or more responses, serializing dicts for convenience."""
        return cls(
            responses=tuple(
                p if isinstance(p, str) else json.dumps(p) for p in payloads
            )
        )

    @property
    def last_prompt(self) -> Prompt | None:
        return self.prompts[-1] if self.prompts else None


@dataclass
class RaisingLlmClient:
    """A provider that fails if it is called at all (Property 11).

    Injected wherever the design claims no LLM call happens: cache hits,
    `get_exhibit`, `list_graveyard`, every gallery render, and the offline seed
    path. If one of those paths ever grew a narration step, this turns a silent
    cost into a failed test.
    """

    model: str = "must-not-be-called"
    calls: int = 0

    def complete_json(self, prompt: Prompt) -> LlmResult:
        self.calls += 1
        raise AssertionError(
            "this code path must not call the LLM, but it built a prompt "
            f"({prompt.template_version=}, {prompt.prompt_hash[:12]=})"
        )


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------
def build_client(config: Config | None = None) -> LlmClient | None:
    """The configured provider, or `None` when narration must use templates.

    `None` is a first-class answer (R-13.8): the demo runs with no key, and the
    narrator reads `None` as "use the deterministic templates and label them".
    """
    cfg = config or load_config()
    if not cfg.llm_enabled:
        log.info(
            "no LLM provider configured; narratives will use deterministic templates"
        )
        return None
    assert cfg.llm_api_key is not None  # llm_enabled implies a key is present
    log.info("LLM provider %s configured with model %s", cfg.llm_provider, cfg.llm_model)
    return OpenAiCompatibleClient(
        api_key=cfg.llm_api_key,
        model=cfg.llm_model,
        base_url=cfg.llm_base_url,
    )
