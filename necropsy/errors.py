"""Closed error taxonomy (design.md section 6, R-13.10, E10).

Every failure reaching a client is one of these codes, in the shared envelope.
An unmapped exception becomes INTERNAL_ERROR with a correlation id; the stack
trace stays server-side.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    INVALID_URL = "INVALID_URL"
    UNSUPPORTED_HOST = "UNSUPPORTED_HOST"
    REPO_NOT_FOUND = "REPO_NOT_FOUND"
    REPO_INACCESSIBLE = "REPO_INACCESSIBLE"
    REPO_EMPTY = "REPO_EMPTY"
    REPO_TOO_LARGE = "REPO_TOO_LARGE"
    RATE_LIMITED = "RATE_LIMITED"
    CALL_BUDGET_EXCEEDED = "CALL_BUDGET_EXCEEDED"
    ANALYSIS_TIMEOUT = "ANALYSIS_TIMEOUT"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    NOT_IN_GRAVEYARD = "NOT_IN_GRAVEYARD"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    OFFLINE_CACHE_MISS = "OFFLINE_CACHE_MISS"


# code -> (http status, retryable)
_SPEC: dict[ErrorCode, tuple[int, bool]] = {
    ErrorCode.INVALID_URL: (400, False),
    ErrorCode.UNSUPPORTED_HOST: (400, False),
    ErrorCode.REPO_NOT_FOUND: (404, False),
    ErrorCode.REPO_INACCESSIBLE: (403, False),
    ErrorCode.REPO_EMPTY: (422, False),
    ErrorCode.REPO_TOO_LARGE: (422, False),
    ErrorCode.RATE_LIMITED: (429, True),
    ErrorCode.CALL_BUDGET_EXCEEDED: (500, False),
    ErrorCode.ANALYSIS_TIMEOUT: (504, True),
    ErrorCode.LLM_UNAVAILABLE: (200, True),
    ErrorCode.NOT_IN_GRAVEYARD: (404, False),
    ErrorCode.UPSTREAM_ERROR: (502, True),
    ErrorCode.INTERNAL_ERROR: (500, False),
    ErrorCode.OFFLINE_CACHE_MISS: (409, False),
}

# Reader-facing next action per code. The UI renders this inside the gallery
# frame, never as an alert and never as a traceback (R-13.9).
NEXT_ACTION: dict[ErrorCode, str] = {
    ErrorCode.INVALID_URL: "Paste a URL shaped like github.com/owner/repo.",
    ErrorCode.UNSUPPORTED_HOST: "NECROPSY reads github.com only.",
    ErrorCode.REPO_NOT_FOUND: "Check the spelling, or try one of the exhibits already on the wall.",
    ErrorCode.REPO_INACCESSIBLE: "GitHub refused this repository. Try a public one.",
    ErrorCode.REPO_EMPTY: "There is no commit history to examine.",
    ErrorCode.REPO_TOO_LARGE: "Pick a smaller repository; this one exceeds the size cap.",
    ErrorCode.RATE_LIMITED: "Every exhibit already on the wall still works. Try again after the reset.",
    ErrorCode.CALL_BUDGET_EXCEEDED: "This is a NECROPSY defect, not your input. Please report it.",
    ErrorCode.ANALYSIS_TIMEOUT: "The analysis ran long. Try again.",
    ErrorCode.LLM_UNAVAILABLE: "Facts and scorecard are intact; the narrative uses templates.",
    ErrorCode.NOT_IN_GRAVEYARD: "This repository has not been analyzed yet. Paste its URL to analyze it.",
    ErrorCode.UPSTREAM_ERROR: "GitHub returned an error. Try again shortly.",
    ErrorCode.INTERNAL_ERROR: "Something broke on our side. The correlation id below identifies it.",
    ErrorCode.OFFLINE_CACHE_MISS: "Cached results only were requested, and this repository is not cached.",
}


class NecropsyError(Exception):
    """Base for every expected failure. Carries its own client envelope."""

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}
        self.correlation_id = uuid.uuid4().hex[:12]

    @property
    def http_status(self) -> int:
        return _SPEC[self.code][0]

    @property
    def retryable(self) -> bool:
        return _SPEC[self.code][1]

    @property
    def next_action(self) -> str:
        return NEXT_ACTION[self.code]

    def to_envelope(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": str(self.code),
                "message": self.message,
                "retryable": self.retryable,
                "next_action": self.next_action,
                "details": self.details,
                "correlation_id": self.correlation_id,
            },
        }


class InvalidUrl(NecropsyError):
    code = ErrorCode.INVALID_URL


class UnsupportedHost(NecropsyError):
    code = ErrorCode.UNSUPPORTED_HOST


class RepoNotFound(NecropsyError):
    code = ErrorCode.REPO_NOT_FOUND


class RepoInaccessible(NecropsyError):
    code = ErrorCode.REPO_INACCESSIBLE


class RepoEmpty(NecropsyError):
    code = ErrorCode.REPO_EMPTY


class RepoTooLarge(NecropsyError):
    code = ErrorCode.REPO_TOO_LARGE


class RateLimited(NecropsyError):
    code = ErrorCode.RATE_LIMITED


class CallBudgetExceeded(NecropsyError):
    code = ErrorCode.CALL_BUDGET_EXCEEDED


class AnalysisTimeout(NecropsyError):
    code = ErrorCode.ANALYSIS_TIMEOUT


class NotInGraveyard(NecropsyError):
    code = ErrorCode.NOT_IN_GRAVEYARD


class UpstreamError(NecropsyError):
    code = ErrorCode.UPSTREAM_ERROR


class OfflineCacheMiss(NecropsyError):
    code = ErrorCode.OFFLINE_CACHE_MISS


def wrap_unexpected(exc: BaseException) -> NecropsyError:
    """Map an unmapped exception to INTERNAL_ERROR without leaking its text."""
    err = NecropsyError(
        "An unexpected internal error occurred.",
        code=ErrorCode.INTERNAL_ERROR,
        details={"exception_type": type(exc).__name__},
    )
    return err
