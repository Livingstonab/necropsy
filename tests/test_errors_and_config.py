"""T-1.2 done-criteria: every error code has an envelope, and no secret escapes.

These were specified as part of T-1.2 and are checked here so the contract in
design.md section 6 and E12 is enforced by the suite rather than by review.
"""

from __future__ import annotations

import dataclasses
import logging

import pytest

from necropsy import errors
from necropsy.config import Config, configure_logging, load_config
from necropsy.errors import ErrorCode, NecropsyError, wrap_unexpected

TOKEN = "ghp_secretvalue1234567890"
LLM_KEY = "sk-secretllmkey0987654321"


# -- errors ----------------------------------------------------------------
@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_code_produces_a_complete_envelope(code: ErrorCode) -> None:
    err = NecropsyError("something happened", code=code, details={"k": "v"})
    envelope = err.to_envelope()["error"]

    assert envelope["code"] == code.value
    assert envelope["message"] == "something happened"
    assert isinstance(envelope["retryable"], bool)
    assert envelope["next_action"], "every code needs a reader-facing next action"
    assert envelope["details"] == {"k": "v"}
    assert len(envelope["correlation_id"]) == 12
    assert 200 <= err.http_status < 600


@pytest.mark.parametrize("code", list(ErrorCode))
def test_every_code_is_rendered_and_mapped(code: ErrorCode) -> None:
    assert code in errors.NEXT_ACTION
    assert code in errors._SPEC


def test_correlation_ids_are_unique_per_error() -> None:
    first = NecropsyError("a")
    second = NecropsyError("a")
    assert first.correlation_id != second.correlation_id


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (errors.InvalidUrl("bad"), ErrorCode.INVALID_URL),
        (errors.UnsupportedHost("bad host"), ErrorCode.UNSUPPORTED_HOST),
        (errors.RepoNotFound("gone"), ErrorCode.REPO_NOT_FOUND),
        (errors.RepoInaccessible("no"), ErrorCode.REPO_INACCESSIBLE),
        (errors.RepoEmpty("empty"), ErrorCode.REPO_EMPTY),
        (errors.RepoTooLarge("big"), ErrorCode.REPO_TOO_LARGE),
        (errors.RateLimited("wait"), ErrorCode.RATE_LIMITED),
        (errors.CallBudgetExceeded("over"), ErrorCode.CALL_BUDGET_EXCEEDED),
        (errors.AnalysisTimeout("slow"), ErrorCode.ANALYSIS_TIMEOUT),
        (errors.NotInGraveyard("absent"), ErrorCode.NOT_IN_GRAVEYARD),
        (errors.UpstreamError("github"), ErrorCode.UPSTREAM_ERROR),
        (errors.OfflineCacheMiss("offline"), ErrorCode.OFFLINE_CACHE_MISS),
    ],
)
def test_subclasses_carry_their_code(exc: NecropsyError, code: ErrorCode) -> None:
    assert exc.code is code
    assert exc.to_envelope()["error"]["code"] == code.value


def test_rate_limited_is_retryable_and_budget_exceeded_is_not() -> None:
    assert errors.RateLimited("x").retryable is True
    assert errors.CallBudgetExceeded("x").retryable is False


def test_unexpected_exceptions_do_not_leak_their_message() -> None:
    secret = ValueError(f"connection string with {TOKEN}")
    wrapped = wrap_unexpected(secret)
    envelope = wrapped.to_envelope()

    assert wrapped.code is ErrorCode.INTERNAL_ERROR
    assert TOKEN not in repr(envelope)
    assert envelope["error"]["details"] == {"exception_type": "ValueError"}


# -- config ----------------------------------------------------------------
def test_config_loads_from_an_empty_environment() -> None:
    cfg = load_config()
    assert cfg.github_token is None
    assert cfg.token_present is False
    assert cfg.llm_enabled is False
    assert cfg.github_call_budget == 10
    assert cfg.rate_limit_floor == 12
    assert cfg.analysis_timeout_seconds == 25
    assert cfg.database_path == "./necropsy.db"


def test_config_reads_secrets_but_never_renders_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", LLM_KEY)
    cfg = load_config()

    assert cfg.github_token == TOKEN
    assert cfg.llm_api_key == LLM_KEY
    assert cfg.llm_enabled is True

    for rendering in (repr(cfg), str(cfg), repr(cfg.public_dict())):
        assert TOKEN not in rendering
        assert LLM_KEY not in rendering

    public = cfg.public_dict()
    assert public["github_token_present"] is True
    assert public["llm_key_present"] is True
    assert "github_token" not in public
    assert "llm_api_key" not in public


def test_public_dict_reports_presence_as_a_boolean_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E12: never a prefix, never a length, never a value."""
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    public = load_config().public_dict()
    assert public["github_token_present"] is True
    for value in public.values():
        assert TOKEN[:4] not in str(value)


def test_logging_warns_once_per_degraded_capability(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    cfg = load_config()

    with caplog.at_level(logging.WARNING, logger="necropsy.config"):
        configure_logging(cfg)

    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 1, "one warning per degraded capability, no more"
    assert "LLM_API_KEY absent" in messages[0]
    assert TOKEN not in caplog.text


def test_unparseable_integer_falls_back_to_the_documented_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_CALL_BUDGET", "not-a-number")
    assert load_config().github_call_budget == 10


def test_config_is_frozen() -> None:
    """A settings snapshot cannot be edited after it is loaded.

    `FrozenInstanceError` by name rather than a blind `Exception`: the point is
    that the dataclass refused the write, not that something went wrong.
    """
    cfg = Config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.github_call_budget = 99  # type: ignore[misc]
