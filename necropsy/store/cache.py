"""Cache semantics: read, write, single-flight, and the two volatile fields (T-1.4).

This module is the only place that decides whether a request is a hit or a miss.
`store/analyses.py` below it knows rows and SQL; `AnalysisService` above it knows
the pipeline. Four rules are implemented here and nowhere else:

  - **Explicit status (R-1.4, R-2.1).** A lookup returns a `CacheLookup` carrying a
    `CacheStatus`, never a bare `Analysis | None`. A caller cannot forget to say
    which path served it, and `hit`/`miss` is a closed set of two.
  - **A version bump misses, it does not delete (R-2.6).** The lookup asks for the
    current `analysis_schema_version` only. The superseded row is still there, is
    still readable, and is reported as `CacheLookup.superseded` so a comparison or a
    `stale_ok` render can use it. Nothing in this module issues a DELETE.
  - **Single flight (R-3.6, E14).** An in-process `dict[cache_key, _Flight]` guarded
    by a lock coalesces concurrent identical requests, so the pipeline body runs
    once and every caller is served from that one result. The `UNIQUE` constraint
    on `analyses.cache_key` plus `ON CONFLICT DO NOTHING` covers the cross-process
    case (web + MCP), which a lock in one process cannot (E3).
  - **`served_at` and `cache_status` are assembled, never stored (R-2.4).** They are
    injected by `Analysis.to_payload()` at response time. No column, no JSON key in
    any stored blob. `persisted_view()` exists so a test can state that as an
    equality rather than an inspection.

A coalesced follower is reported as a hit: by the time it is served, the row is in
the cache and that caller spent zero GitHub calls and zero LLM calls, which is what
`cache_status` tells a reader (R-2.2). `CacheResult.coalesced` keeps the distinction
available for `/api/health` and the job record without widening the status set.

Parameterized SQL only, inherited from `store/analyses.py` (E4).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar, cast

from necropsy import ANALYSIS_SCHEMA_VERSION
from necropsy.models.analysis import Analysis
from necropsy.store import analyses as store
from necropsy.store.analyses import AnalysisRow
from necropsy.store.db import Database

log = logging.getLogger("necropsy.store.cache")

T = TypeVar("T")

#: The two fields a served payload carries that the stored row does not (R-2.4).
VOLATILE_FIELDS = frozenset({"cache_status", "served_at"})

#: Raw payload map as handed to the store: decoded dict, or already-canonical JSON.
RawPayloads = dict[str, Any] | str

#: A cache-miss pipeline body. Returns the assembled analysis and its raw payloads.
Pipeline = Callable[[], "tuple[Analysis, RawPayloads]"]


class CacheStatus(StrEnum):
    """How a response was served. A closed set of exactly two values (R-1.4)."""

    HIT = "hit"
    MISS = "miss"


def served_now() -> datetime:
    """The response-assembly clock. Never the analysis clock, which is `as_of` (E6)."""
    return datetime.now(UTC)


def persisted_view(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload minus the two injected fields: what the row actually holds."""
    return {k: v for k, v in payload.items() if k not in VOLATILE_FIELDS}


# --------------------------------------------------------------------------
# single flight
# --------------------------------------------------------------------------
@dataclass
class _Flight:
    """One in-progress call. The leader fills it; waiters read it."""

    done: threading.Event = field(default_factory=threading.Event)
    value: Any = None
    error: BaseException | None = None
    waiters: int = 0


@dataclass(frozen=True)
class FlightOutcome(Generic[T]):
    """What a `SingleFlight.run` call produced, and whether this caller ran it."""

    value: T
    leader: bool
    waiters: int = 0

    @property
    def coalesced(self) -> bool:
        return not self.leader


class SingleFlight:
    """Coalesces concurrent calls that share a key (R-3.6).

    The first caller for a key runs the function; every caller that arrives while
    it is running waits and is served the same result. A function that raises
    propagates the same exception to all of them, and the key is released, so a
    failed analysis is retryable rather than poisoned. Nothing is cached here:
    once the call finishes the key is gone, and the durable cache is SQLite.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}
        self._coalesced_total = 0

    @property
    def in_flight(self) -> int:
        with self._lock:
            return len(self._flights)

    @property
    def coalesced_total(self) -> int:
        """How many callers were served by someone else's run. Observability only."""
        with self._lock:
            return self._coalesced_total

    def pending_keys(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._flights)

    def run(self, key: str, fn: Callable[[], T]) -> FlightOutcome[T]:
        own: _Flight | None = None
        joined: _Flight | None = None
        with self._lock:
            running = self._flights.get(key)
            if running is None:
                own = _Flight()
                self._flights[key] = own
            else:
                running.waiters += 1
                self._coalesced_total += 1
                joined = running

        if joined is not None:
            log.debug("single-flight: coalescing a request for %s", key)
            joined.done.wait()
            if joined.error is not None:
                raise joined.error
            value: T = joined.value
            return FlightOutcome(value=value, leader=False)

        assert own is not None  # one branch or the other, never neither
        try:
            own.value = fn()
        except BaseException as exc:  # re-raised to every waiter, then released
            own.error = exc
            raise
        finally:
            with self._lock:
                self._flights.pop(key, None)
            own.done.set()

        return FlightOutcome(value=own.value, leader=True, waiters=own.waiters)


# --------------------------------------------------------------------------
# lookup and result records
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CacheLookup:
    """The answer to one cache question, with the reason attached."""

    status: CacheStatus
    cache_key: str
    canonical_url: str
    schema_version: int
    row: AnalysisRow | None = None
    #: Newest stored row for this URL at any SHA or schema version. Present on a
    #: miss when earlier work survives (R-2.6); the source for stale-ok serving.
    superseded: AnalysisRow | None = None
    #: True when the read was deliberately skipped (`force_refresh`, R-11.4).
    forced: bool = False

    @property
    def is_hit(self) -> bool:
        return self.status is CacheStatus.HIT

    def require_row(self) -> AnalysisRow:
        if self.row is None:
            raise LookupError(f"cache miss for {self.cache_key}")
        return self.row

    def analysis(self) -> Analysis | None:
        """Rehydrate the stored analysis, or None on a miss."""
        return store.load_analysis(self.row) if self.row is not None else None


@dataclass(frozen=True)
class CacheResult:
    """A served analysis and the status to report for it."""

    status: CacheStatus
    analysis: Analysis
    row: AnalysisRow | None = None
    #: Served from another thread's in-flight run rather than its own (R-3.6).
    coalesced: bool = False

    @property
    def is_hit(self) -> bool:
        return self.status is CacheStatus.HIT

    def payload(self, served_at: datetime | None = None) -> dict[str, Any]:
        """The response. The only place `cache_status` and `served_at` exist (R-2.4)."""
        return self.analysis.to_payload(
            cache_status=cast(Literal["hit", "miss"], self.status.value),
            served_at=served_at or served_now(),
        )


# --------------------------------------------------------------------------
# the cache
# --------------------------------------------------------------------------
class AnalysisCache:
    """Read/write API over the `analyses` table, plus the single-flight map.

    One instance per process is the intent: the single-flight map is in-process
    state, so sharing the instance is what makes coalescing work.
    """

    def __init__(
        self,
        db: Database,
        *,
        schema_version: int = ANALYSIS_SCHEMA_VERSION,
        single_flight: SingleFlight | None = None,
    ) -> None:
        self.db = db
        self.schema_version = schema_version
        self.single_flight = single_flight or SingleFlight()

    # -- keys ---------------------------------------------------------------
    def key_for(self, canonical_url: str, last_commit_sha: str) -> str:
        return store.cache_key(canonical_url, last_commit_sha, self.schema_version)

    # -- reads --------------------------------------------------------------
    def lookup(
        self,
        canonical_url: str,
        last_commit_sha: str,
        *,
        force_refresh: bool = False,
    ) -> CacheLookup:
        """The cache question: is there a row for this URL, SHA and schema version?

        A hit costs one indexed read and nothing else (E14). A miss costs a second
        indexed read to report the superseded row, which is free relative to the
        pipeline it is about to authorize.
        """
        key = self.key_for(canonical_url, last_commit_sha)
        url = canonical_url.strip().lower()
        conn = self.db.connect()

        if force_refresh:
            return CacheLookup(
                status=CacheStatus.MISS,
                cache_key=key,
                canonical_url=url,
                schema_version=self.schema_version,
                superseded=self._newest_any_version(conn, url),
                forced=True,
            )

        row = store.get_by_cache_key(conn, key)
        if row is not None:
            return CacheLookup(
                status=CacheStatus.HIT,
                cache_key=key,
                canonical_url=url,
                schema_version=self.schema_version,
                row=row,
            )
        return CacheLookup(
            status=CacheStatus.MISS,
            cache_key=key,
            canonical_url=url,
            schema_version=self.schema_version,
            superseded=self._newest_any_version(conn, url),
        )

    def lookup_by_url(self, canonical_url: str, *, stale_ok: bool = False) -> CacheLookup:
        """Zero-network read for gallery navigation and `get_exhibit` (R-2.5).

        No SHA is resolved, so no GitHub call is made. With `stale_ok` the newest
        row at any schema version is accepted, which is how a superseded analysis
        stays visible instead of vanishing on a bump (R-2.6).
        """
        url = canonical_url.strip().lower()
        conn = self.db.connect()
        row = store.get_latest_for_url(
            conn, url, schema_version=None if stale_ok else self.schema_version
        )
        status = CacheStatus.HIT if row is not None else CacheStatus.MISS
        return CacheLookup(
            status=status,
            cache_key=row.cache_key if row is not None else "",
            canonical_url=url,
            schema_version=self.schema_version,
            row=row,
            superseded=None if row is not None else self._newest_any_version(conn, url),
        )

    def _newest_any_version(self, conn: sqlite3.Connection, url: str) -> AnalysisRow | None:
        return store.get_latest_for_url(conn, url, schema_version=None)

    # -- writes -------------------------------------------------------------
    def write(self, analysis: Analysis, raw_payloads: RawPayloads) -> AnalysisRow:
        """Persist a completed analysis in one transaction (design 4.6).

        `INSERT ... ON CONFLICT(cache_key) DO NOTHING` then read back, so a row
        written by another process wins and both callers are served the same bytes
        (E3, R-2.4). A failed or partial analysis is never passed here (E3).
        """
        with self.db.transaction() as conn:
            return store.save_analysis(conn, analysis, raw_payloads)

    # -- the combined path --------------------------------------------------
    def get_or_compute(
        self,
        canonical_url: str,
        last_commit_sha: str,
        pipeline: Pipeline,
        *,
        force_refresh: bool = False,
    ) -> CacheResult:
        """Serve from cache, or run `pipeline` exactly once and serve its result.

        Order matters: the cache read happens before `pipeline` is ever called, so
        no fetch, no prompt and no LLM call can precede a hit (E1, E3).
        """
        found = self.lookup(canonical_url, last_commit_sha, force_refresh=force_refresh)
        if found.is_hit:
            row = found.require_row()
            return CacheResult(
                status=CacheStatus.HIT, analysis=store.load_analysis(row), row=row
            )

        key = found.cache_key

        def body() -> CacheResult:
            # Re-check under leadership: a concurrent process may have written the
            # row between the lookup above and winning the flight.
            if not force_refresh:
                conn = self.db.connect()
                existing = store.get_by_cache_key(conn, key)
                if existing is not None:
                    return CacheResult(
                        status=CacheStatus.HIT,
                        analysis=store.load_analysis(existing),
                        row=existing,
                    )
            analysis, raw = pipeline()
            row = self.write(analysis, raw)
            return CacheResult(
                status=CacheStatus.MISS, analysis=store.load_analysis(row), row=row
            )

        outcome = self.single_flight.run(key, body)
        result = outcome.value
        if outcome.coalesced:
            # This caller ran nothing: zero GitHub calls, zero LLM calls (R-2.2).
            return CacheResult(
                status=CacheStatus.HIT,
                analysis=result.analysis,
                row=result.row,
                coalesced=True,
            )
        return result
