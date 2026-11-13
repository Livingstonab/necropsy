"""Environment configuration (R-18, E12).

Rules enforced here:
  - every tunable has a working default, so the app starts with an empty env
  - secrets are read from the environment and never rendered, logged or returned
  - a missing optional capability logs exactly one warning naming the degradation
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger("necropsy.config")

_SECRET_FIELDS = frozenset({"github_token", "llm_api_key"})


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s=%r is not an integer; using default %d", name, raw, default)
        return default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def _env_secret(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


@dataclass(frozen=True)
class Config:
    """Immutable settings snapshot. Never place a secret in __repr__ (E12)."""

    github_token: str | None = None
    llm_provider: str = "none"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    database_path: str = "./necropsy.db"
    max_repo_size_kb: int = 51_200
    max_repo_files: int = 5_000
    github_call_budget: int = 10
    rate_limit_floor: int = 12
    analysis_timeout_seconds: int = 25
    readme_excerpt_chars: int = 4_000
    manifest_excerpt_chars: int = 2_000
    log_level: str = "INFO"
    port: int = 5_000
    warnings: tuple[str, ...] = field(default=())

    # -- capability flags ---------------------------------------------------
    @property
    def token_present(self) -> bool:
        return self.github_token is not None

    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none" and self.llm_api_key is not None

    def __repr__(self) -> str:  # pragma: no cover - trivial, but security-relevant
        shown = {
            k: ("<redacted>" if k in _SECRET_FIELDS and v else v)
            for k, v in self.public_dict().items()
        }
        return f"Config({shown})"

    __str__ = __repr__

    def public_dict(self) -> dict[str, object]:
        """Every field, with secrets replaced by booleans. Safe to log or return."""
        return {
            "github_token_present": self.token_present,
            "llm_provider": self.llm_provider,
            "llm_key_present": self.llm_api_key is not None,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "database_path": self.database_path,
            "max_repo_size_kb": self.max_repo_size_kb,
            "max_repo_files": self.max_repo_files,
            "github_call_budget": self.github_call_budget,
            "rate_limit_floor": self.rate_limit_floor,
            "analysis_timeout_seconds": self.analysis_timeout_seconds,
            "readme_excerpt_chars": self.readme_excerpt_chars,
            "manifest_excerpt_chars": self.manifest_excerpt_chars,
            "log_level": self.log_level,
            "port": self.port,
        }


def load_dotenv(path: str = ".env") -> None:
    """Load `KEY=value` lines from a `.env` file into the environment.

    Dependency-free on purpose: a single optional file should not pull in a
    package. A variable already set in the real environment always wins, so a
    deployment's dashboard settings are never overridden by a stray committed
    file (and `.env` is gitignored regardless). Missing file is a no-op.

    Called once at the start of `load_config`, so `python -m necropsy.cli serve`
    and every other entry point pick up a local `.env` with no extra step.
    """
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:  # pragma: no cover - an unreadable .env must not crash startup
        log.warning("could not read %s; continuing with the process environment", path)


def load_config() -> Config:
    """Build a Config from the environment, warning once per degraded capability."""
    load_dotenv()
    warnings: list[str] = []

    token = _env_secret("GITHUB_TOKEN")
    if token is None:
        warnings.append(
            "GITHUB_TOKEN absent: GitHub calls are unauthenticated (60 req/hour). "
            "Cached exhibits and the demo graveyard are unaffected."
        )

    provider = _env_str("LLM_PROVIDER", "none").lower()
    key = _env_secret("LLM_API_KEY")
    if provider == "none":
        warnings.append(
            "LLM_PROVIDER=none: narratives use deterministic templates. "
            "Facts and scorecard are unaffected."
        )
    elif key is None:
        warnings.append(
            f"LLM_PROVIDER={provider} but LLM_API_KEY absent: falling back to "
            "deterministic template narratives."
        )

    cfg = Config(
        github_token=token,
        llm_provider=provider,
        llm_api_key=key,
        llm_model=_env_str("LLM_MODEL", "gpt-4o-mini"),
        llm_base_url=_env_str("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        database_path=_env_str("DATABASE_PATH", "./necropsy.db"),
        max_repo_size_kb=_env_int("MAX_REPO_SIZE_KB", 51_200),
        max_repo_files=_env_int("MAX_REPO_FILES", 5_000),
        github_call_budget=_env_int("GITHUB_CALL_BUDGET", 10),
        rate_limit_floor=_env_int("RATE_LIMIT_FLOOR", 12),
        analysis_timeout_seconds=_env_int("ANALYSIS_TIMEOUT_SECONDS", 25),
        readme_excerpt_chars=_env_int("README_EXCERPT_CHARS", 4_000),
        manifest_excerpt_chars=_env_int("MANIFEST_EXCERPT_CHARS", 2_000),
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        port=_env_int("PORT", 5_000),
        warnings=tuple(warnings),
    )
    return cfg


def configure_logging(cfg: Config) -> None:
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    for warning in cfg.warnings:
        log.warning(warning)
