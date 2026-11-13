"""All GitHub I/O (R-14, E2). The only module permitted to make a GitHub request.

Five rules are enforced here rather than left to pipeline discipline:

  - **The call counter lives in the client, not the caller (E2).** Every attempt is
    charged, including a retry, so call 11 raises `CallBudgetExceeded` by
    construction rather than by convention. A pipeline that grows a call fails a
    test instead of quietly spending a request. `GITHUB_CALL_BUDGET` is a ceiling
    the environment can lower but never raise.
  - **Observed rate limit, never guessed (R-14.2).** Every response's headers become
    a `rate_limit_ledger` row before the status is inspected, which is what lets the
    floor pre-check and the header indicator read a measurement. `begin_analysis()`
    refuses below the floor having spent zero calls (R-14.3).
  - **Read once, with an explicit `per_page` (E2).** `per_page` is a required keyword
    and a `rel="next"` link is reported, never followed. There is no pagination loop
    in this module and there must never be one.
  - **One retry, charged (R-14.5, R-14.6).** A 5xx, a connection error or a short
    honored `Retry-After` is retried exactly once, and that attempt costs a call.
  - **No clone, no archive, ever (E4).** The base URL is the REST API, the only verb
    is GET, and a tarball/zipball/archive/codeload path is refused before any call.

Timeouts are explicit (5 s connect, 10 s read) because a hung socket would eat the
20 s cache-miss budget in NFR-2 with nothing to show for it.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx

from necropsy.config import Config, load_config
from necropsy.errors import (
    CallBudgetExceeded,
    RateLimited,
    RepoEmpty,
    RepoInaccessible,
    RepoNotFound,
    UpstreamError,
)
from necropsy.store import ledger
from necropsy.store.db import Database

log = logging.getLogger("necropsy.github.client")

API_ROOT = "https://api.github.com"
ACCEPT = "application/vnd.github+json"
API_VERSION = "2022-11-28"
USER_AGENT = "necropsy/1.0 (+read-only post-mortem tool)"

CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 10.0

#: Hard ceiling on GitHub calls per analysis (E2). The declared plan uses 9; the
#: tenth is reserved for exactly one retry. The environment may lower this and
#: must not be able to raise it.
GITHUB_CALL_BUDGET = 10

#: Below this observed `remaining`, a fresh analysis is refused outright (R-14.3).
RATE_LIMIT_FLOOR = 12

#: A `Retry-After` longer than this cannot be honored inside a 25 s analysis
#: budget, so it becomes RATE_LIMITED immediately instead of a sleep (E10).
MAX_RETRY_AFTER_SECONDS = 5

#: GitHub's own bounds for `per_page`. Out of range is a programming error, so it
#: is refused before a call is charged.
MIN_PER_PAGE = 1
MAX_PER_PAGE = 100

#: Path fragments that would mean downloading repository content in bulk (E4).
ARCHIVE_FRAGMENTS = ("/tarball", "/zipball", "/archive", "codeload", ".tar.gz", ".zip")


def build_http_client(token: str | None) -> httpx.Client:
    """One reused connection pool, explicit timeouts, token only when present.

    A token raises the operator's rate limit. It is never required, never
    requested from a visitor, and never rendered anywhere (P3, E12).
    """
    headers = {
        "Accept": ACCEPT,
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": USER_AGENT,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(
        timeout=httpx.Timeout(
            connect=CONNECT_TIMEOUT_SECONDS,
            read=READ_TIMEOUT_SECONDS,
            write=READ_TIMEOUT_SECONDS,
            pool=CONNECT_TIMEOUT_SECONDS,
        ),
        follow_redirects=False,
        headers=headers,
    )


# --------------------------------------------------------------------------
# responses
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class GitHubResponse:
    """One answered request. `body` is parsed JSON, or None for a tolerated status."""

    endpoint_key: str
    path: str
    status: int
    body: Any = None
    etag: str | None = None
    not_modified: bool = False
    attempts: int = 1
    has_next_page: bool = False

    @property
    def ok(self) -> bool:
        return self.not_modified or 200 <= self.status < 300

    @property
    def absent(self) -> bool:
        """A tolerated 404/403: the resource does not exist or is not readable."""
        return not self.ok


@dataclass
class BudgetScope:
    """A view of one analysis's budget, handed out by `begin_analysis()`.

    The counter itself lives on the client, which is what makes the ceiling
    unavoidable. This record exists so the pipeline can report what it spent.
    """

    client: GitHubClient
    budget: int
    endpoints: list[str] = field(default_factory=list)

    @property
    def calls_used(self) -> int:
        return self.client.calls_used

    @property
    def remaining(self) -> int:
        return self.client.calls_remaining


# --------------------------------------------------------------------------
# the client
# --------------------------------------------------------------------------
class GitHubClient:
    """A single reused `httpx.Client` plus the budget, ledger and ETag machinery."""

    def __init__(
        self,
        db: Database,
        config: Config | None = None,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
        budget: int | None = None,
        floor: int | None = None,
    ) -> None:
        self.db = db
        self.config = config or load_config()
        self._sleep = sleep or time.sleep
        requested = self.config.github_call_budget if budget is None else budget
        #: Never above the hard ceiling, whatever the environment says (E2).
        self.budget = max(1, min(requested, GITHUB_CALL_BUDGET))
        self.floor = self.config.rate_limit_floor if floor is None else floor
        self.calls_used = 0
        self.endpoints_called: list[str] = []
        self._owns_http = http is None
        self._http = http or build_http_client(self.config.github_token)

    # -- identity and lifecycle ---------------------------------------------
    @property
    def token_present(self) -> bool:
        """A boolean, which is the most anything may ever learn about the token."""
        return self.config.token_present

    @property
    def calls_remaining(self) -> int:
        return max(0, self.budget - self.calls_used)

    def __repr__(self) -> str:
        return (
            f"GitHubClient(token_present={self.token_present}, "
            f"budget={self.budget}, calls_used={self.calls_used})"
        )

    __str__ = __repr__

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- budget lifecycle ---------------------------------------------------
    def begin_analysis(self) -> BudgetScope:
        """Open a per-analysis budget, refusing up front if the ledger is low.

        This is guard 2 in design 4.2: it runs before the first call, so a
        refused analysis spends nothing. Cached exhibits are unaffected because
        they never reach this method (R-14.3, P4).
        """
        self.check_rate_limit_floor()
        self.calls_used = 0
        self.endpoints_called = []
        return BudgetScope(client=self, budget=self.budget)

    def check_rate_limit_floor(self) -> None:
        """Refuse a fresh analysis when the last measurement is under the floor.

        A stale reading must not block forever. Three cases make a reading
        irrelevant and are ignored:
          - it was taken with no token but we now hold one (the limit jumped from
            60 to 5,000, so an old "4 remaining" says nothing about now);
          - it was taken with a token but we now hold none (the reverse);
          - its rate-limit window has already reset, so the count is spent.
        In any of those, we let the analysis proceed; the per-call budget still
        caps it, and the very next response writes a fresh, accurate ledger row.
        """
        state = ledger.latest(self.db.connect())
        if state is None or not state.below_floor(self.floor):
            return
        if state.token_present != self.token_present:
            log.info("ignoring a stale rate-limit reading taken under different auth")
            return
        if state.retry_after_seconds() == 0:
            log.info("ignoring a rate-limit reading whose window has already reset")
            return
        raise RateLimited(
            "GitHub's rate limit for this host is nearly exhausted, so NECROPSY is "
            "not starting a new analysis. Every exhibit already on the wall still works.",
            details={
                "rate_limit_remaining": state.remaining,
                "rate_limit_limit": state.limit,
                "floor": self.floor,
                "reset_at": state.reset_at,
                "retry_after_seconds": state.retry_after_seconds(),
                "calls_spent": 0,
                "token_present": self.token_present,
            },
        )

    def rate_limit_state(self) -> ledger.RateLimitState | None:
        return ledger.latest(self.db.connect())

    def _charge(self, path: str) -> None:
        """Spend one call, or refuse. Raising here is the whole point (E2)."""
        if self.calls_used >= self.budget:
            raise CallBudgetExceeded(
                "This analysis tried to make more GitHub calls than its budget allows.",
                details={
                    "budget": self.budget,
                    "calls_used": self.calls_used,
                    "endpoints": list(self.endpoints_called),
                    "attempted": path,
                },
            )
        self.calls_used += 1
        self.endpoints_called.append(path)

    # -- the one request method --------------------------------------------
    def get(
        self,
        path: str,
        *,
        per_page: int,
        params: Mapping[str, Any] | None = None,
        tolerate_404: bool = False,
        tolerate_403: bool = False,
    ) -> GitHubResponse:
        """GET one endpoint, once, within this analysis's budget.

        `tolerate_404` / `tolerate_403` name statuses the caller treats as data
        ("no README" is a 404, not a failure). A 403 that is really a rate limit
        is never tolerated: it is reported, because the next call would fail too.
        Every other non-2xx becomes a mapped `NecropsyError` (design 6).
        """
        self._reject_archive(path)
        if not MIN_PER_PAGE <= per_page <= MAX_PER_PAGE:
            raise ValueError(
                f"per_page must be between {MIN_PER_PAGE} and {MAX_PER_PAGE}, got {per_page}"
            )

        query: dict[str, Any] = {**(params or {}), "per_page": per_page}
        endpoint_key = _endpoint_key(path, query)
        stored = self._load_etag(endpoint_key)
        headers = {"If-None-Match": stored[0]} if stored else {}

        attempt = 0
        while True:
            attempt += 1
            self._charge(path)
            try:
                response = self._http.get(
                    f"{API_ROOT}{path}", params=query, headers=headers
                )
            except httpx.TransportError as exc:
                if attempt == 1:
                    log.warning("transport error on %s; retrying once", path)
                    continue
                raise UpstreamError(
                    "GitHub could not be reached.",
                    details={
                        "path": path,
                        "reason": type(exc).__name__,
                        "attempts": attempt,
                        "calls_spent": self.calls_used,
                    },
                ) from exc

            observation = self._observe(path, response)
            self._record(observation)
            status = response.status_code

            if status == 304 and stored is not None:
                log.debug("%s not modified; reusing the stored body", path)
                return GitHubResponse(
                    endpoint_key=endpoint_key,
                    path=path,
                    status=304,
                    body=json.loads(stored[1]),
                    etag=stored[0],
                    not_modified=True,
                    attempts=attempt,
                )

            if 200 <= status < 300:
                etag = response.headers.get("ETag")
                if etag:
                    self._store_etag(endpoint_key, etag, response.text)
                return GitHubResponse(
                    endpoint_key=endpoint_key,
                    path=path,
                    status=status,
                    body=self._parse(response, path),
                    etag=etag,
                    attempts=attempt,
                    has_next_page=_has_next_page(response),
                )

            rate_limited = _looks_rate_limited(response, observation)

            if status == 404 and tolerate_404:
                log.debug("%s returned a tolerated 404", path)
                return GitHubResponse(
                    endpoint_key=endpoint_key, path=path, status=404, attempts=attempt
                )
            if status == 403 and tolerate_403 and not rate_limited:
                log.debug("%s returned a tolerated 403", path)
                return GitHubResponse(
                    endpoint_key=endpoint_key, path=path, status=403, attempts=attempt
                )

            if rate_limited:
                retry_after = _retry_after(response)
                if (
                    retry_after is not None
                    and retry_after <= MAX_RETRY_AFTER_SECONDS
                    and attempt == 1
                ):
                    log.warning("%s asked us to wait %ds; honoring once", path, retry_after)
                    self._sleep(float(retry_after))
                    continue
                raise self._rate_limited(path, observation, retry_after, attempt)

            if status >= 500 and attempt == 1:
                log.warning("%s returned %d; retrying once", path, status)
                continue

            raise self._map_error(path, response, attempt)

    # -- guards -------------------------------------------------------------
    @staticmethod
    def _reject_archive(path: str) -> None:
        """E4: NECROPSY reads the API. It never downloads repository archives."""
        lowered = path.lower()
        if any(fragment in lowered for fragment in ARCHIVE_FRAGMENTS):
            raise ValueError(
                "NECROPSY never downloads repository archives or clones a repository "
                f"(E4); refused {path}. Use the git trees endpoint to list files."
            )

    # -- helpers ------------------------------------------------------------
    def _load_etag(self, endpoint_key: str) -> tuple[str, str] | None:
        try:
            return ledger.get_etag(self.db.connect(), endpoint_key)
        except Exception:  # pragma: no cover - a cold cache must never fail a call
            log.debug("etag lookup failed for %s; proceeding unconditionally", endpoint_key)
            return None

    def _store_etag(self, endpoint_key: str, etag: str, body: str) -> None:
        try:
            with self.db.transaction() as conn:
                ledger.put_etag(conn, endpoint_key, etag, body)
        except Exception:  # pragma: no cover - conditional caching is best effort
            log.debug("etag store failed for %s", endpoint_key)

    @staticmethod
    def _parse(response: httpx.Response, path: str) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                "GitHub returned a response that was not JSON.",
                details={"path": path, "status": response.status_code},
            ) from exc

    def _observe(self, path: str, response: httpx.Response) -> ledger.RateLimitObservation:
        headers = response.headers
        return ledger.RateLimitObservation(
            endpoint=path,
            status=response.status_code,
            limit=_header_int(headers, "x-ratelimit-limit"),
            remaining=_header_int(headers, "x-ratelimit-remaining"),
            used=_header_int(headers, "x-ratelimit-used"),
            reset_at=_header_epoch(headers, "x-ratelimit-reset"),
            token_present=self.token_present,
        )

    def _record(self, observation: ledger.RateLimitObservation) -> None:
        """A ledger write must not be able to fail the call it only describes."""
        try:
            with self.db.transaction() as conn:
                ledger.record(conn, observation)
        except Exception:  # pragma: no cover - observability is not the product
            log.debug("ledger write failed for %s", observation.endpoint)

    def _rate_limited(
        self,
        path: str,
        observation: ledger.RateLimitObservation,
        retry_after: int | None,
        attempts: int,
    ) -> RateLimited:
        reset_at = observation.reset_at
        return RateLimited(
            "GitHub's rate limit has been reached. Every exhibit already on the wall "
            "still works.",
            details={
                "path": path,
                "status": observation.status,
                "rate_limit_remaining": observation.remaining,
                "rate_limit_limit": observation.limit,
                "reset_at": reset_at.isoformat() if reset_at else None,
                "retry_after_seconds": (
                    retry_after if retry_after is not None else _seconds_until(reset_at)
                ),
                "attempts": attempts,
                "calls_spent": self.calls_used,
                "token_present": observation.token_present,
            },
        )

    def _map_error(self, path: str, response: httpx.Response, attempts: int) -> Exception:
        """Translate a GitHub status into the closed taxonomy (design 6)."""
        status = response.status_code
        details: dict[str, Any] = {
            "path": path,
            "status": status,
            "attempts": attempts,
            "calls_spent": self.calls_used,
        }

        if status == 404:
            return RepoNotFound(
                "No public repository was found at that address.", details=details
            )
        if status == 409:
            return RepoEmpty(
                "That repository has no commits, so there is nothing to examine.",
                details=details,
            )
        if status in {401, 403, 451}:
            return RepoInaccessible(
                "GitHub refused access to that repository.", details=details
            )
        if status >= 500:
            return UpstreamError("GitHub returned a server error.", details=details)
        return UpstreamError("GitHub returned an unexpected status.", details=details)


# --------------------------------------------------------------------------
# the client that proves a path made no call
# --------------------------------------------------------------------------
class NoNetworkGitHubClient(GitHubClient):
    """A client that raises if it is used at all.

    Injected wherever the design claims zero network calls: cache hits,
    `get_exhibit`, `list_graveyard`, every gallery render, and
    `seed-graveyard --offline` (R-11.5, R-17.3, Property 11). The assertion is
    the test; if such a path quietly grew a fetch, this fails loudly.
    """

    def __init__(self, db: Database, config: Config | None = None) -> None:
        super().__init__(db, config, http=httpx.Client(base_url=API_ROOT))
        self._http.close()

    def get(  # type: ignore[override]
        self,
        path: str,
        *,
        per_page: int,
        params: Mapping[str, Any] | None = None,
        tolerate_404: bool = False,
        tolerate_403: bool = False,
    ) -> GitHubResponse:
        raise AssertionError(
            f"this code path must not call GitHub, but it requested {path}"
        )

    def begin_analysis(self) -> BudgetScope:
        return BudgetScope(client=self, budget=0)


# --------------------------------------------------------------------------
# header and key helpers
# --------------------------------------------------------------------------
def _endpoint_key(path: str, query: Mapping[str, Any]) -> str:
    """The ETag store key: the request identity, stable across processes."""
    suffix = f"?{urlencode(sorted((k, str(v)) for k, v in query.items()))}" if query else ""
    return f"GET {path}{suffix}"


def _looks_rate_limited(
    response: httpx.Response, observation: ledger.RateLimitObservation
) -> bool:
    """Distinguish "slow down" from "forbidden", which get different codes."""
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    if observation.remaining == 0:
        return True
    body = (response.text or "")[:500].lower()
    return "rate limit" in body or "secondary rate" in body


def _retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(0, int(float(raw.strip())))
    except ValueError:
        return None


def _has_next_page(response: httpx.Response) -> bool:
    """Report a further page; never follow it. Pagination loops are forbidden (E2)."""
    return 'rel="next"' in response.headers.get("link", "")


def _seconds_until(moment: datetime | None) -> int | None:
    if moment is None:
        return None
    return max(0, int((moment - datetime.now(UTC)).total_seconds()))


def _header_int(headers: httpx.Headers, name: str) -> int | None:
    raw = headers.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _header_epoch(headers: httpx.Headers, name: str) -> datetime | None:
    seconds = _header_int(headers, name)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)
