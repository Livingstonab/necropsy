"""T-1.3: exhibits, jobs and the rate-limit ledger (R-5, R-6, R-14)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from necropsy.errors import ErrorCode
from necropsy.store import analyses as analyses_store
from necropsy.store import exhibits as exhibits_store
from necropsy.store import jobs as jobs_store
from necropsy.store import ledger as ledger_store
from necropsy.store.db import Database
from tests.factories import make_analysis, make_facts, make_raw_payloads


def _install(db: Database, owner: str, name: str, *, is_demo: bool = True, order: int = 10,
             note: str | None = None, **fact_overrides: object) -> None:
    facts = make_facts(owner, name, **fact_overrides)
    analysis = make_analysis(facts=facts)
    with db.transaction() as conn:
        row = analyses_store.save_analysis(conn, analysis, make_raw_payloads(owner, name))
        exhibits_store.upsert_exhibit(
            conn,
            repo_id=row.repo_id,
            analysis_id=row.id,
            slug=analysis.slug,
            plaque_title=f"{owner}/{name}",
            is_demo=is_demo,
            curated=is_demo,
            curator_note=note,
            display_order=order,
        )


# -- exhibits --------------------------------------------------------------
def test_exhibit_round_trip_by_slug(db: Database) -> None:
    _install(db, "octocat", "abandoned-cart", note="Died with its Gemfile pinned.")
    found = exhibits_store.get_by_slug(db.connect(), "octocat--abandoned-cart")
    assert found is not None
    assert found.is_demo and found.curated
    assert found.curator_note == "Died with its Gemfile pinned."
    assert found.analysis.facts.owner == "octocat"
    assert found.analysis.scorecard.score == found.row.score


def test_exhibit_lookup_by_canonical_url(db: Database) -> None:
    _install(db, "octocat", "abandoned-cart")
    assert exhibits_store.get_by_canonical_url(db.connect(), "github.com/OctoCat/Abandoned-Cart")
    assert exhibits_store.get_by_canonical_url(db.connect(), "github.com/nobody/nothing") is None


def test_upsert_is_idempotent_and_keeps_one_exhibit_per_repo(db: Database) -> None:
    """R-5.5: re-seeding must not duplicate exhibits."""
    _install(db, "octocat", "abandoned-cart")
    _install(db, "octocat", "abandoned-cart")
    assert exhibits_store.count_exhibits(db.connect()) == 1


def test_upsert_never_demotes_a_curated_exhibit(db: Database) -> None:
    _install(db, "octocat", "abandoned-cart", is_demo=True, note="curator note")
    _install(db, "octocat", "abandoned-cart", is_demo=False, note=None)
    found = exhibits_store.get_by_slug(db.connect(), "octocat--abandoned-cart")
    assert found is not None
    assert found.is_demo and found.curated
    assert found.curator_note == "curator note"


def test_sorts_are_server_side_and_closed_to_three_options(db: Database) -> None:
    _install(
        db, "a", "easy", order=2,
        has_license=True, has_ci=True, ci_is_legacy_only=False, has_tests=True,
        test_file_count=40, doc_page_count=12, deprecated_dependency_count=0,
        fresh_dependency_count=14, days_since_last_commit=120,
        last_commit_at=datetime(2025, 11, 1, tzinfo=UTC),
    )
    _install(
        db, "b", "hard", order=1,
        has_license=False, has_ci=False, has_tests=False, test_file_count=0,
        doc_page_count=0, readme_chars=0, deprecated_dependency_count=9,
        fresh_dependency_count=0, days_since_last_commit=3_000,
        last_commit_at=datetime(2017, 1, 1, tzinfo=UTC),
    )
    conn = db.connect()

    assert [e.slug for e in exhibits_store.list_exhibits(conn, sort="curated")] == [
        "b--hard",
        "a--easy",
    ]
    assert [e.slug for e in exhibits_store.list_exhibits(conn, sort="deadest")] == [
        "b--hard",
        "a--easy",
    ]
    hardest = exhibits_store.list_exhibits(conn, sort="hardest")
    assert hardest[0].slug == "b--hard"
    assert hardest[0].analysis.scorecard.score > hardest[1].analysis.scorecard.score

    with pytest.raises(ValueError):
        exhibits_store.list_exhibits(conn, sort="score DESC; DROP TABLE exhibits")


def test_list_paginates_and_filters_demo_entries(db: Database) -> None:
    _install(db, "a", "one", order=1, is_demo=True)
    _install(db, "b", "two", order=2, is_demo=False)
    conn = db.connect()
    assert len(exhibits_store.list_exhibits(conn, limit=1)) == 1
    assert len(exhibits_store.list_exhibits(conn, limit=1, offset=1)) == 1
    assert [e.slug for e in exhibits_store.list_exhibits(conn, demo_only=True)] == ["a--one"]
    assert exhibits_store.count_exhibits(conn, demo_only=True) == 1


# -- jobs ------------------------------------------------------------------
def test_job_lifecycle_to_done(db: Database) -> None:
    analysis = make_analysis()
    with db.transaction() as conn:
        row = analyses_store.save_analysis(conn, analysis, make_raw_payloads())
        job = jobs_store.create(conn, analysis.facts.canonical_url)
        assert job.state == "queued" and not job.finished
        jobs_store.mark_running(conn, job.job_id, cache_key=analysis.cache_key)
        jobs_store.mark_done(conn, job.job_id, analysis_id=row.id)
        done = jobs_store.get(conn, job.job_id)

    assert done is not None
    assert done.state == "done" and done.finished
    assert done.result_analysis_id == row.id
    assert done.cache_key == analysis.cache_key
    assert done.error_code is None


def test_job_lifecycle_to_failed_records_a_taxonomy_code(db: Database) -> None:
    with db.transaction() as conn:
        job = jobs_store.create(conn, "github.com/a/b")
        jobs_store.mark_failed(
            conn, job.job_id, code=ErrorCode.RATE_LIMITED, message="Try after the reset."
        )
        failed = jobs_store.get(conn, job.job_id)

    assert failed is not None
    assert failed.state == "failed" and failed.finished
    assert failed.error_code == "RATE_LIMITED"
    assert failed.result_analysis_id is None


def test_unknown_job_is_none(db: Database) -> None:
    assert jobs_store.get(db.connect(), "nope") is None


# -- ledger ----------------------------------------------------------------
def test_ledger_records_and_reports_the_latest_observation(db: Database) -> None:
    reset = datetime(2026, 3, 1, 13, 0, 0, tzinfo=UTC)
    with db.transaction() as conn:
        ledger_store.record(
            conn,
            ledger_store.RateLimitObservation(
                endpoint="GET /repos/a/b",
                status=200,
                limit=60,
                remaining=42,
                used=18,
                reset_at=reset,
                token_present=False,
                observed_at="2026-03-01T12:00:00+00:00",
            ),
        )
        ledger_store.record(
            conn,
            ledger_store.RateLimitObservation(
                endpoint="GET /repos/a/b/languages",
                status=200,
                limit=60,
                remaining=41,
                used=19,
                reset_at=reset,
                token_present=False,
                observed_at="2026-03-01T12:00:01+00:00",
            ),
        )

    state = ledger_store.latest(db.connect())
    assert state is not None
    assert state.remaining == 41
    assert state.token_present is False
    assert state.reset_datetime == reset
    assert ledger_store.count_observations(db.connect()) == 2


def test_floor_check_needs_a_measurement(db: Database) -> None:
    assert ledger_store.latest(db.connect()) is None, "no calls yet means no state"

    with db.transaction() as conn:
        ledger_store.record(
            conn,
            ledger_store.RateLimitObservation(
                endpoint="GET /rate_limit", status=200, limit=60, remaining=None
            ),
        )
    unmeasured = ledger_store.latest(db.connect())
    assert unmeasured is not None
    assert unmeasured.below_floor(12) is False, "absent data is not evidence of exhaustion"

    with db.transaction() as conn:
        ledger_store.record(
            conn,
            ledger_store.RateLimitObservation(
                endpoint="GET /rate_limit", status=200, limit=60, remaining=11
            ),
        )
    low = ledger_store.latest(db.connect())
    assert low is not None and low.below_floor(12) is True


def test_retry_after_seconds_is_floored_at_zero(db: Database) -> None:
    now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
    state = ledger_store.RateLimitState(
        observed_at=now.isoformat(),
        endpoint="GET /repos/a/b",
        status=403,
        limit=60,
        remaining=0,
        used=60,
        reset_at=datetime(2026, 3, 1, 11, 0, 0, tzinfo=UTC).isoformat(),
        token_present=False,
    )
    assert state.retry_after_seconds(now=now) == 0
    future = state.__class__(**{**state.__dict__, "reset_at": "2026-03-01T12:30:00+00:00"})
    assert future.retry_after_seconds(now=now) == 1_800


# -- etags -----------------------------------------------------------------
def test_etag_store_overwrites_by_endpoint_key(db: Database) -> None:
    conn_key = "GET /repos/a/b"
    with db.transaction() as conn:
        assert ledger_store.get_etag(conn, conn_key) is None
        ledger_store.put_etag(conn, conn_key, 'W/"v1"', '{"n":1}')
        ledger_store.put_etag(conn, conn_key, 'W/"v2"', '{"n":2}')
    assert ledger_store.get_etag(db.connect(), conn_key) == ('W/"v2"', '{"n":2}')
