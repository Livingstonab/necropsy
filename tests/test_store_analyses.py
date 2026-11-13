"""T-1.3: cache key, round-trip fidelity, conflict handling (R-2, E3)."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest

from necropsy import ANALYSIS_SCHEMA_VERSION
from necropsy.models.narrative import LlmNarrative
from necropsy.store import analyses as store
from necropsy.store.db import Database
from tests.factories import SHA, make_analysis, make_facts, make_raw_payloads


# -- cache key -------------------------------------------------------------
def test_cache_key_is_sha256_of_the_declared_material() -> None:
    import hashlib

    expected = hashlib.sha256(f"github.com/a/b@{SHA}@1".encode()).hexdigest()
    assert store.cache_key("github.com/a/b", SHA, 1) == expected


def test_cache_key_lowercases_the_url_but_not_the_sha() -> None:
    assert store.cache_key("GitHub.com/OctoCat/Repo", SHA, 1) == store.cache_key(
        "github.com/octocat/repo", SHA, 1
    )
    assert store.cache_key("github.com/a/b", SHA.upper(), 1) != store.cache_key(
        "github.com/a/b", SHA, 1
    )


def test_cache_key_separates_url_sha_and_version() -> None:
    assert store.cache_key("github.com/a/b", SHA, 1) != store.cache_key("github.com/a/b", SHA, 2)
    assert store.cache_key("github.com/a/b", SHA, 1) != store.cache_key("github.com/a/c", SHA, 1)


def test_cache_key_is_stable_in_a_fresh_process() -> None:
    """Done-criteria: the key cannot depend on hash randomization or process state."""
    script = textwrap.dedent(
        f"""
        from necropsy.store.analyses import cache_key
        print(cache_key("github.com/a/b", "{SHA}", 1))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == store.cache_key("github.com/a/b", SHA, 1)


# -- round trip ------------------------------------------------------------
def test_save_and_load_preserves_the_payload(db: Database) -> None:
    """Done-criteria: a written analysis reads back with a byte-identical payload
    apart from cache_status and served_at (R-2.4)."""
    analysis = make_analysis()
    original = analysis.to_payload(cache_status="miss")

    with db.transaction() as conn:
        row = store.save_analysis(conn, analysis, make_raw_payloads())

    loaded = store.load_analysis(row)
    served = loaded.to_payload(cache_status="hit")

    volatile = {"cache_status", "served_at"}
    assert set(original) == set(served)
    for key in original:
        if key in volatile:
            continue
        assert json.dumps(served[key], sort_keys=True) == json.dumps(
            original[key], sort_keys=True
        ), f"section {key!r} changed across the round trip"

    assert original["cache_status"] == "miss"
    assert served["cache_status"] == "hit"


def test_stored_json_columns_round_trip_byte_for_byte(db: Database) -> None:
    analysis = make_analysis()
    with db.transaction() as conn:
        row = store.save_analysis(conn, analysis, make_raw_payloads())

    assert row.normalized_json == analysis.facts.model_dump_json()
    assert row.scorecard_json == analysis.scorecard.model_dump_json()
    assert row.narrative_json == analysis.narrative.model_dump_json()
    reloaded = store.load_analysis(row)
    assert reloaded.facts.model_dump_json() == row.normalized_json
    assert reloaded.scorecard.model_dump_json() == row.scorecard_json


def test_raw_payloads_are_stored_for_zero_call_renormalization(db: Database) -> None:
    raw = make_raw_payloads()
    with db.transaction() as conn:
        row = store.save_analysis(conn, make_analysis(), raw)
    assert row.raw_payloads() == raw


def test_row_columns_mirror_the_analysis(db: Database) -> None:
    analysis = make_analysis()
    with db.transaction() as conn:
        row = store.save_analysis(conn, analysis, make_raw_payloads())

    assert row.cache_key == analysis.cache_key
    assert row.canonical_url == "github.com/octocat/abandoned-cart"
    assert row.last_commit_sha == SHA
    assert row.as_of == analysis.facts.as_of.isoformat()
    assert row.score == analysis.scorecard.score
    assert row.band == analysis.scorecard.band.key
    assert row.github_calls_used == 9
    assert row.analysis_schema_version == ANALYSIS_SCHEMA_VERSION


# -- conflict and lookup ---------------------------------------------------
def test_second_insert_with_the_same_key_does_not_duplicate(db: Database) -> None:
    analysis = make_analysis()
    with db.transaction() as conn:
        first = store.save_analysis(conn, analysis, make_raw_payloads())
    with db.transaction() as conn:
        second = store.save_analysis(conn, analysis, {"repo": {"changed": True}})

    assert first.id == second.id
    assert second.raw_payloads() == make_raw_payloads(), "the winning row must not be overwritten"
    with db.transaction() as conn:
        assert store.count_analyses(conn) == 1


def test_repo_identity_is_inserted_once(db: Database) -> None:
    with db.transaction() as conn:
        store.save_analysis(conn, make_analysis(), make_raw_payloads())
        store.save_analysis(
            conn, make_analysis(sha="b" * 40), make_raw_payloads()
        )
        rows = conn.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"]
    assert rows == 1


def test_get_by_cache_key_and_id(db: Database) -> None:
    analysis = make_analysis()
    with db.transaction() as conn:
        row = store.save_analysis(conn, analysis, make_raw_payloads())

    conn = db.connect()
    assert store.get_by_cache_key(conn, analysis.cache_key) == row
    assert store.get_by_id(conn, row.id) == row
    assert store.get_by_cache_key(conn, "absent") is None
    assert store.get_by_id(conn, 4_242) is None


def test_get_latest_for_url_returns_the_newest_row(db: Database) -> None:
    older = make_analysis(sha="1" * 40)
    newer = make_analysis(sha="2" * 40).model_copy(
        update={"created_at": older.created_at.replace(year=2026, month=4)}
    )
    with db.transaction() as conn:
        store.save_analysis(conn, older, make_raw_payloads())
        store.save_analysis(conn, newer, make_raw_payloads())

    found = store.get_latest_for_url(db.connect(), "github.com/OctoCat/Abandoned-Cart")
    assert found is not None
    assert found.last_commit_sha == "2" * 40


def test_schema_version_bump_is_a_miss_and_keeps_the_old_row(db: Database) -> None:
    """R-2.6: a bump invalidates by miss, never by deletion."""
    v1 = make_analysis(schema_version=1)
    with db.transaction() as conn:
        store.save_analysis(conn, v1, make_raw_payloads())

    key_v2 = store.cache_key(v1.facts.canonical_url, v1.facts.last_commit_sha, 2)
    conn = db.connect()
    assert store.get_by_cache_key(conn, key_v2) is None, "a bumped version must miss"
    assert store.get_by_cache_key(conn, v1.cache_key) is not None, "the old row must survive"
    assert store.get_latest_for_url(conn, v1.facts.canonical_url, schema_version=2) is None
    assert store.get_latest_for_url(conn, v1.facts.canonical_url, schema_version=1) is not None


# -- failed narrative ------------------------------------------------------
def test_failed_llm_persists_facts_and_scorecard_with_no_narrative(db: Database) -> None:
    """R-3.4: the narrative stage failing must not cost the facts or the score."""
    analysis = make_analysis(llm_status="failed")
    with db.transaction() as conn:
        row = store.save_analysis(conn, analysis, make_raw_payloads())

    assert row.narrative_json is None
    assert row.llm_status == "failed"
    loaded = store.load_analysis(row)
    assert loaded.facts == analysis.facts
    assert loaded.scorecard == analysis.scorecard
    assert loaded.narrative.epitaph.source == "unavailable"
    assert loaded.narrative.llm_status == "failed"


def test_unavailable_narrative_is_deterministic() -> None:
    first = LlmNarrative.unavailable("reason")
    second = LlmNarrative.unavailable("reason")
    assert first.model_dump_json() == second.model_dump_json()


def test_data_gaps_in_the_payload_are_derived_from_facts_and_scorecard(db: Database) -> None:
    """A gap must survive the round trip without being carried in a column."""
    facts = make_facts(manifest_found=False, data_gaps=["readme"])
    analysis = make_analysis(facts=facts)
    with db.transaction() as conn:
        row = store.save_analysis(conn, analysis, make_raw_payloads())

    gaps = store.load_analysis(row).to_payload(cache_status="hit")["provenance"]["data_gaps"]
    assert gaps[0] == "readme"
    assert "dependencies" in gaps, "the neutral dependency default must be declared"


@pytest.mark.parametrize("value", ["", "   "])
def test_cache_key_rejects_nothing_but_normalizes_whitespace(value: str) -> None:
    """Whitespace cannot create a second identity for the same repository."""
    assert store.cache_key(f" github.com/a/b{value}", f" {SHA} ", 1) == store.cache_key(
        "github.com/a/b", SHA, 1
    )
