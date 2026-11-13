"""T-1.4: cache status, bump-is-a-miss, single flight, volatile fields.

Covers R-2.1 through R-2.6 and R-3.6. Offline, no keys, stdlib threading only
(E11). Nothing here touches the network, and no test spawns a watch process.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from necropsy import ANALYSIS_SCHEMA_VERSION
from necropsy.models.analysis import Analysis
from necropsy.store import analyses as store
from necropsy.store.cache import (
    VOLATILE_FIELDS,
    AnalysisCache,
    CacheStatus,
    SingleFlight,
    persisted_view,
)
from necropsy.store.db import Database
from tests.factories import SHA, make_analysis, make_raw_payloads

URL = "github.com/octocat/abandoned-cart"
TIMEOUT = 5.0


def canonical(payload: dict[str, Any]) -> str:
    """Byte view of the part of a payload that must survive a round trip."""
    return store.canonical_json(persisted_view(payload))


def seed(cache: AnalysisCache, analysis: Analysis | None = None) -> Analysis:
    written = analysis or make_analysis()
    cache.write(written, make_raw_payloads())
    return written


# -- status ----------------------------------------------------------------
def test_cache_status_is_a_closed_set_of_two() -> None:
    """R-1.4: the reported status is `hit` or `miss`, and nothing else."""
    assert [s.value for s in CacheStatus] == ["hit", "miss"]
    assert str(CacheStatus.HIT) == "hit"


def test_lookup_reports_a_miss_on_an_empty_cache(db: Database) -> None:
    found = AnalysisCache(db).lookup(URL, SHA)
    assert found.status is CacheStatus.MISS
    assert not found.is_hit
    assert found.row is None
    assert found.superseded is None
    assert found.analysis() is None
    assert found.cache_key == store.cache_key(URL, SHA, ANALYSIS_SCHEMA_VERSION)
    with pytest.raises(LookupError):
        found.require_row()


def test_lookup_reports_a_hit_once_the_row_exists(db: Database) -> None:
    """R-2.1: a row matching URL, SHA and schema version is served from store."""
    cache = AnalysisCache(db)
    written = seed(cache)

    found = cache.lookup(URL, SHA)
    assert found.is_hit
    assert found.status is CacheStatus.HIT
    assert found.require_row().cache_key == written.cache_key
    assert found.cache_key == written.cache_key


def test_lookup_is_case_and_whitespace_insensitive_on_the_url(db: Database) -> None:
    cache = AnalysisCache(db)
    seed(cache)
    assert cache.lookup("  GitHub.com/OctoCat/Abandoned-Cart  ", SHA).is_hit


def test_lookup_by_url_needs_no_sha(db: Database) -> None:
    """R-2.5: gallery navigation resolves no SHA, so it makes zero network calls."""
    cache = AnalysisCache(db)
    seed(cache)

    found = cache.lookup_by_url("github.com/OctoCat/Abandoned-Cart")
    assert found.is_hit
    assert found.require_row().last_commit_sha == SHA
    assert cache.lookup_by_url("github.com/nobody/nothing").status is CacheStatus.MISS


# -- R-2.4: identical payload except the two volatile fields ---------------
def test_hit_payload_is_identical_to_the_stored_payload(db: Database) -> None:
    """Done-criteria: a hit differs from the stored payload only in `served_at`
    and `cache_status` (R-2.4, design Property 9)."""
    cache = AnalysisCache(db)
    written = make_analysis()
    stored_payload = written.to_payload(cache_status="miss")
    cache.write(written, make_raw_payloads())

    served = cache.lookup(URL, SHA)
    assert served.is_hit
    hit_payload = store.load_analysis(served.require_row()).to_payload(cache_status="hit")

    assert canonical(hit_payload) == canonical(stored_payload)
    differing = {k for k in stored_payload if stored_payload[k] != hit_payload[k]}
    assert differing <= VOLATILE_FIELDS
    assert hit_payload["cache_status"] == "hit"
    assert stored_payload["cache_status"] == "miss"


def test_two_serves_of_one_row_differ_only_in_served_at(db: Database) -> None:
    cache = AnalysisCache(db)
    seed(cache)

    first = cache.get_or_compute(URL, SHA, _unreachable_pipeline)
    time.sleep(0.001)
    second = cache.get_or_compute(URL, SHA, _unreachable_pipeline)

    assert first.is_hit and second.is_hit
    assert canonical(first.payload()) == canonical(second.payload())
    assert first.payload()["cache_status"] == second.payload()["cache_status"] == "hit"


def test_the_volatile_fields_are_never_persisted(db: Database) -> None:
    """They are assembled at response time, so neither a column nor a stored blob
    may carry them (R-2.4, design 4.6)."""
    cache = AnalysisCache(db)
    seed(cache)
    conn = db.connect()

    columns = {r["name"] for r in conn.execute("PRAGMA table_info(analyses)").fetchall()}
    assert VOLATILE_FIELDS.isdisjoint(columns)

    row = cache.lookup(URL, SHA).require_row()
    blobs = (row.normalized_json, row.scorecard_json, row.narrative_json or "", row.raw_github_json)
    for field in VOLATILE_FIELDS:
        assert all(field not in blob for blob in blobs)

    payload = cache.get_or_compute(URL, SHA, _unreachable_pipeline).payload()
    assert set(payload) >= VOLATILE_FIELDS
    assert VOLATILE_FIELDS.isdisjoint(persisted_view(payload))


# -- R-2.6: a bump is a miss, never a deletion -----------------------------
def test_schema_bump_misses_and_the_old_row_survives(db: Database) -> None:
    """Done-criteria: bumping `analysis_schema_version` causes a miss while the
    superseded row stays readable (R-2.6)."""
    v1_cache = AnalysisCache(db, schema_version=1)
    v1 = seed(v1_cache, make_analysis(schema_version=1))
    v1_payload = v1.to_payload(cache_status="miss")

    v2_cache = AnalysisCache(db, schema_version=2)
    bumped = v2_cache.lookup(URL, SHA)
    assert bumped.status is CacheStatus.MISS
    assert bumped.cache_key != v1.cache_key

    # The old row was not deleted: it is reported, countable, and still loadable.
    assert bumped.superseded is not None
    assert bumped.superseded.cache_key == v1.cache_key
    assert store.count_analyses(db.connect()) == 1

    survivor = v1_cache.lookup(URL, SHA)
    assert survivor.is_hit
    reread = store.load_analysis(survivor.require_row()).to_payload(cache_status="hit")
    assert canonical(reread) == canonical(v1_payload)
    assert reread["analysis_schema_version"] == 1

    # Under the new version the old row is invisible by default and visible to a
    # deliberate stale-ok read, which is what keeps it available for comparison.
    assert v2_cache.lookup_by_url(URL).status is CacheStatus.MISS
    stale = v2_cache.lookup_by_url(URL, stale_ok=True)
    assert stale.is_hit
    assert stale.require_row().analysis_schema_version == 1


def test_a_bumped_write_adds_a_row_instead_of_replacing_one(db: Database) -> None:
    v1_cache = AnalysisCache(db, schema_version=1)
    seed(v1_cache, make_analysis(schema_version=1))
    v2_cache = AnalysisCache(db, schema_version=2)
    seed(v2_cache, make_analysis(schema_version=2))

    assert store.count_analyses(db.connect()) == 2
    assert v1_cache.lookup(URL, SHA).is_hit
    assert v2_cache.lookup(URL, SHA).is_hit


# -- get_or_compute --------------------------------------------------------
def _unreachable_pipeline() -> tuple[Analysis, dict[str, Any]]:
    """E1/E3: nothing may run the pipeline on a cache-hit path."""
    raise AssertionError("the pipeline must not run on a cache hit")


def _counting_pipeline(
    counter: list[str], analysis: Analysis | None = None
) -> Any:
    written = analysis or make_analysis()

    def pipeline() -> tuple[Analysis, dict[str, Any]]:
        counter.append("ran")
        return written, make_raw_payloads()

    return pipeline


def test_miss_runs_the_pipeline_once_and_caches_the_result(db: Database) -> None:
    """R-3.1, R-3.3: a miss executes the pipeline and writes exactly one row."""
    cache = AnalysisCache(db)
    runs: list[str] = []

    first = cache.get_or_compute(URL, SHA, _counting_pipeline(runs))
    assert first.status is CacheStatus.MISS
    assert not first.coalesced
    assert runs == ["ran"]
    assert store.count_analyses(db.connect()) == 1

    second = cache.get_or_compute(URL, SHA, _counting_pipeline(runs))
    assert second.status is CacheStatus.HIT
    assert runs == ["ran"], "a hit must not re-enter the pipeline"
    assert canonical(first.payload()) == canonical(second.payload())


def test_a_hit_never_reaches_the_pipeline(db: Database) -> None:
    cache = AnalysisCache(db)
    seed(cache)
    assert cache.get_or_compute(URL, SHA, _unreachable_pipeline).is_hit


def test_force_refresh_bypasses_the_read_without_duplicating_the_row(db: Database) -> None:
    """R-11.4: a forced refresh skips the cache read and still writes at most one
    row per cache_key."""
    cache = AnalysisCache(db)
    seed(cache)
    runs: list[str] = []

    forced = cache.lookup(URL, SHA, force_refresh=True)
    assert forced.status is CacheStatus.MISS
    assert forced.forced
    assert forced.superseded is not None

    result = cache.get_or_compute(URL, SHA, _counting_pipeline(runs), force_refresh=True)
    assert runs == ["ran"]
    assert result.status is CacheStatus.MISS
    assert store.count_analyses(db.connect()) == 1, "ON CONFLICT DO NOTHING, not a second row"


def test_a_failed_pipeline_writes_nothing_and_stays_retryable(db: Database) -> None:
    """E3: nothing is cached for a failed analysis, and the key is released."""
    cache = AnalysisCache(db)

    def failing() -> tuple[Analysis, dict[str, Any]]:
        raise RuntimeError("fetch exploded")

    with pytest.raises(RuntimeError):
        cache.get_or_compute(URL, SHA, failing)

    assert store.count_analyses(db.connect()) == 0
    assert cache.single_flight.in_flight == 0

    runs: list[str] = []
    retried = cache.get_or_compute(URL, SHA, _counting_pipeline(runs))
    assert retried.status is CacheStatus.MISS
    assert runs == ["ran"]


# -- R-3.6: single flight --------------------------------------------------
def test_two_threads_on_one_key_run_the_pipeline_exactly_once(db: Database) -> None:
    """Done-criteria: concurrent identical requests execute the pipeline body once
    and both callers are served from that single result (R-3.6, E14)."""
    cache = AnalysisCache(db)
    runs: list[str] = []
    runs_lock = threading.Lock()
    inside = threading.Event()
    release = threading.Event()

    def pipeline() -> tuple[Analysis, dict[str, Any]]:
        with runs_lock:
            runs.append("ran")
        inside.set()
        assert release.wait(TIMEOUT), "the leader was never released"
        return make_analysis(), make_raw_payloads()

    results: dict[str, Any] = {}
    errors: dict[str, BaseException] = {}

    def worker(name: str) -> None:
        try:
            results[name] = cache.get_or_compute(URL, SHA, pipeline)
        except BaseException as exc:  # surfaced in the main thread below
            errors[name] = exc

    leader = threading.Thread(target=worker, args=("leader",), daemon=True)
    leader.start()
    assert inside.wait(TIMEOUT), "the leader never entered the pipeline"

    follower = threading.Thread(target=worker, args=("follower",), daemon=True)
    follower.start()

    deadline = time.monotonic() + TIMEOUT
    while cache.single_flight.coalesced_total == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert cache.single_flight.coalesced_total == 1, "the second request did not coalesce"

    release.set()
    leader.join(TIMEOUT)
    follower.join(TIMEOUT)

    assert not errors, errors
    assert runs == ["ran"], "the pipeline body must execute exactly once"
    assert store.count_analyses(db.connect()) == 1

    served_leader, served_follower = results["leader"], results["follower"]
    assert served_leader.status is CacheStatus.MISS
    assert not served_leader.coalesced
    assert served_follower.status is CacheStatus.HIT
    assert served_follower.coalesced, "the follower ran nothing of its own"
    assert canonical(served_leader.payload()) == canonical(served_follower.payload())
    assert cache.single_flight.in_flight == 0


def test_single_flight_serves_one_result_to_many_waiters() -> None:
    flight = SingleFlight()
    runs: list[str] = []
    release = threading.Event()
    started = threading.Event()

    def slow() -> str:
        runs.append("ran")
        started.set()
        assert release.wait(TIMEOUT)
        return "value"

    outcomes: list[Any] = []
    lock = threading.Lock()

    def call() -> None:
        outcome = flight.run("k", slow)
        with lock:
            outcomes.append(outcome)

    first = threading.Thread(target=call, daemon=True)
    first.start()
    assert started.wait(TIMEOUT)
    others = [threading.Thread(target=call, daemon=True) for _ in range(3)]
    for t in others:
        t.start()

    deadline = time.monotonic() + TIMEOUT
    while flight.coalesced_total < 3 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert flight.coalesced_total == 3
    assert flight.pending_keys() == frozenset({"k"})

    release.set()
    first.join(TIMEOUT)
    for t in others:
        t.join(TIMEOUT)

    assert runs == ["ran"]
    assert [o.value for o in outcomes] == ["value"] * 4
    assert sum(1 for o in outcomes if o.leader) == 1
    assert sum(1 for o in outcomes if o.coalesced) == 3
    assert flight.in_flight == 0


def test_single_flight_does_not_coalesce_different_keys() -> None:
    flight = SingleFlight()
    runs: list[str] = []
    assert flight.run("a", lambda: runs.append("a")).leader
    assert flight.run("b", lambda: runs.append("b")).leader
    assert runs == ["a", "b"]
    assert flight.coalesced_total == 0


def test_single_flight_propagates_the_error_to_every_waiter() -> None:
    flight = SingleFlight()
    release = threading.Event()
    started = threading.Event()
    raised: list[BaseException] = []
    lock = threading.Lock()

    def boom() -> str:
        started.set()
        assert release.wait(TIMEOUT)
        raise ValueError("pipeline failed")

    def call() -> None:
        try:
            flight.run("k", boom)
        except BaseException as exc:
            with lock:
                raised.append(exc)

    leader = threading.Thread(target=call, daemon=True)
    leader.start()
    assert started.wait(TIMEOUT)
    follower = threading.Thread(target=call, daemon=True)
    follower.start()

    deadline = time.monotonic() + TIMEOUT
    while flight.coalesced_total == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    release.set()
    leader.join(TIMEOUT)
    follower.join(TIMEOUT)

    assert len(raised) == 2
    assert all(isinstance(exc, ValueError) for exc in raised)
    assert raised[0] is raised[1], "both callers see the one failure"
    assert flight.in_flight == 0, "a failure must release the key for a retry"


def test_sequential_calls_after_the_flight_closes_run_again() -> None:
    flight = SingleFlight()
    runs: list[str] = []
    for _ in range(3):
        flight.run("k", lambda: runs.append("ran"))
    assert runs == ["ran"] * 3, "single flight coalesces concurrency, it is not a cache"
