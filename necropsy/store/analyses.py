"""The cache: analysis rows keyed by `cache_key` (R-2, E3).

`cache_key = sha256(canonical_url_lowercased + "@" + last_commit_sha + "@" +
analysis_schema_version)`, with a UNIQUE constraint behind it. Writes use
`INSERT ... ON CONFLICT(cache_key) DO NOTHING` and then read back, so the web
process and the MCP process cannot duplicate or race a row (E3).

Round-trip fidelity is the other contract here. An `Analysis` is persisted as
canonical JSON and rebuilt from that JSON, so what a reader is served on a
cache hit is the stored payload, not a re-derived approximation (R-2.4).

SQL is parameterized everywhere. A string-formatted query is a defect (E4).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from necropsy import ANALYSIS_SCHEMA_VERSION
from necropsy.models.analysis import Analysis, Provenance, RepoRef
from necropsy.models.facts import NormalizedFacts
from necropsy.models.narrative import LlmNarrative
from necropsy.models.scorecard import Scorecard
from necropsy.store.db import utc_now_iso

NARRATIVE_UNAVAILABLE_REASON = (
    "The narrative stage did not complete for this analysis. Facts and scorecard "
    "below are unaffected."
)


def cache_key(
    canonical_url: str,
    last_commit_sha: str,
    schema_version: int = ANALYSIS_SCHEMA_VERSION,
) -> str:
    """The cache identity (E3). Pure, and stable across processes and machines."""
    material = f"{canonical_url.strip().lower()}@{last_commit_sha.strip()}@{schema_version}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    """Sorted, separator-fixed JSON. Byte-stability is the point, not prettiness."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class AnalysisRow:
    """An `analyses` row, verbatim. The JSON columns stay as stored text."""

    id: int
    repo_id: int
    cache_key: str
    canonical_url: str
    last_commit_sha: str
    last_commit_at: str
    as_of: str
    analysis_schema_version: int
    scorecard_version: int
    prompt_template_version: int | None
    raw_github_json: str
    normalized_json: str
    scorecard_json: str
    narrative_json: str | None
    score: int
    band: str
    llm_status: str
    llm_model: str | None
    github_calls_used: int
    duration_ms: int
    created_at: str

    @classmethod
    def from_sqlite(cls, row: sqlite3.Row) -> AnalysisRow:
        return cls(
            id=int(row["id"]),
            repo_id=int(row["repo_id"]),
            cache_key=row["cache_key"],
            canonical_url=row["canonical_url"],
            last_commit_sha=row["last_commit_sha"],
            last_commit_at=row["last_commit_at"],
            as_of=row["as_of"],
            analysis_schema_version=int(row["analysis_schema_version"]),
            scorecard_version=int(row["scorecard_version"]),
            prompt_template_version=(
                int(row["prompt_template_version"])
                if row["prompt_template_version"] is not None
                else None
            ),
            raw_github_json=row["raw_github_json"],
            normalized_json=row["normalized_json"],
            scorecard_json=row["scorecard_json"],
            narrative_json=row["narrative_json"],
            score=int(row["score"]),
            band=row["band"],
            llm_status=row["llm_status"],
            llm_model=row["llm_model"],
            github_calls_used=int(row["github_calls_used"]),
            duration_ms=int(row["duration_ms"]),
            created_at=row["created_at"],
        )

    def raw_payloads(self) -> dict[str, Any]:
        """The stored raw GitHub payloads, so re-normalization costs zero calls."""
        decoded: dict[str, Any] = json.loads(self.raw_github_json)
        return decoded


# --------------------------------------------------------------------------
# repos
# --------------------------------------------------------------------------
def upsert_repo(conn: sqlite3.Connection, ref: RepoRef) -> int:
    """Return the repo id, inserting the identity on first sight."""
    conn.execute(
        """
        INSERT INTO repos (canonical_url, owner, name, html_url, first_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(canonical_url) DO NOTHING
        """,
        (ref.canonical_url, ref.owner, ref.name, ref.html_url, utc_now_iso()),
    )
    row = conn.execute(
        "SELECT id FROM repos WHERE canonical_url = ?", (ref.canonical_url,)
    ).fetchone()
    if row is None:  # pragma: no cover - the insert above guarantees a row
        raise sqlite3.IntegrityError(f"repo row missing for {ref.canonical_url}")
    return int(row["id"])


def get_repo_ref(conn: sqlite3.Connection, repo_id: int) -> RepoRef | None:
    row = conn.execute("SELECT owner, name FROM repos WHERE id = ?", (repo_id,)).fetchone()
    if row is None:
        return None
    return RepoRef(owner=row["owner"], name=row["name"])


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------
_SELECT = "SELECT * FROM analyses"


def get_by_cache_key(conn: sqlite3.Connection, key: str) -> AnalysisRow | None:
    """The cache lookup. One indexed read, and the only question a hit asks."""
    row = conn.execute(f"{_SELECT} WHERE cache_key = ?", (key,)).fetchone()
    return AnalysisRow.from_sqlite(row) if row else None


def get_by_id(conn: sqlite3.Connection, analysis_id: int) -> AnalysisRow | None:
    row = conn.execute(f"{_SELECT} WHERE id = ?", (analysis_id,)).fetchone()
    return AnalysisRow.from_sqlite(row) if row else None


def get_latest_for_url(
    conn: sqlite3.Connection,
    canonical_url: str,
    *,
    schema_version: int | None = ANALYSIS_SCHEMA_VERSION,
) -> AnalysisRow | None:
    """Newest analysis for a repository, ignoring the SHA.

    This is the zero-network read behind gallery navigation and `get_exhibit`
    (R-2.5). Pass `schema_version=None` to accept a row written under an older
    schema, which is how a superseded row stays readable for comparison (R-2.6).
    """
    if schema_version is None:
        row = conn.execute(
            f"{_SELECT} WHERE canonical_url = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (canonical_url.lower(),),
        ).fetchone()
    else:
        row = conn.execute(
            f"{_SELECT} WHERE canonical_url = ? AND analysis_schema_version = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (canonical_url.lower(), schema_version),
        ).fetchone()
    return AnalysisRow.from_sqlite(row) if row else None


def count_analyses(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM analyses").fetchone()["n"])


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------
def insert_if_absent(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    analysis: Analysis,
    raw_payloads: dict[str, Any] | str,
) -> AnalysisRow:
    """Write the analysis unless its `cache_key` is already present, then read back.

    The read-back is not belt-and-braces: when a concurrent process won the
    insert, the row it wrote is the one both callers must be served, which is
    what keeps "same cache_key, same payload" true across processes (E3, R-2.4).
    """
    raw_json = raw_payloads if isinstance(raw_payloads, str) else canonical_json(raw_payloads)
    facts = analysis.facts
    prov = analysis.provenance
    narrative_json = (
        None
        if prov.llm_status == "failed"
        else analysis.narrative.model_dump_json()
    )

    conn.execute(
        """
        INSERT INTO analyses (
            repo_id, cache_key, canonical_url, last_commit_sha, last_commit_at, as_of,
            analysis_schema_version, scorecard_version, prompt_template_version,
            raw_github_json, normalized_json, scorecard_json, narrative_json,
            score, band, llm_status, llm_model, github_calls_used, duration_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cache_key) DO NOTHING
        """,
        (
            repo_id,
            analysis.cache_key,
            facts.canonical_url,
            facts.last_commit_sha,
            facts.last_commit_at.isoformat(),
            facts.as_of.isoformat(),
            prov.analysis_schema_version,
            analysis.scorecard.scorecard_version,
            prov.prompt_template_version,
            raw_json,
            facts.model_dump_json(),
            analysis.scorecard.model_dump_json(),
            narrative_json,
            analysis.scorecard.score,
            analysis.scorecard.band.key,
            prov.llm_status,
            prov.llm_model,
            prov.github_calls_used,
            prov.duration_ms,
            analysis.created_at.isoformat(),
        ),
    )
    stored = get_by_cache_key(conn, analysis.cache_key)
    if stored is None:  # pragma: no cover - the insert above guarantees a row
        raise sqlite3.IntegrityError(f"analysis row missing for {analysis.cache_key}")
    return stored


def save_analysis(
    conn: sqlite3.Connection,
    analysis: Analysis,
    raw_payloads: dict[str, Any] | str,
) -> AnalysisRow:
    """Upsert the repo identity, then insert the analysis if absent."""
    repo_id = upsert_repo(conn, analysis.repo)
    return insert_if_absent(
        conn, repo_id=repo_id, analysis=analysis, raw_payloads=raw_payloads
    )


# --------------------------------------------------------------------------
# rehydration
# --------------------------------------------------------------------------
def load_analysis(row: AnalysisRow, *, fixture_source: str | None = None) -> Analysis:
    """Rebuild the `Analysis` a row holds.

    `fixture_source` is a read-time annotation (which committed fixture served
    this, for the provenance footer), not a stored field: the row is the same
    whether it was seeded or fetched.
    """
    facts = NormalizedFacts.model_validate_json(row.normalized_json)
    scorecard = Scorecard.model_validate_json(row.scorecard_json)
    narrative = (
        LlmNarrative.model_validate_json(row.narrative_json)
        if row.narrative_json
        else LlmNarrative.unavailable(NARRATIVE_UNAVAILABLE_REASON)
    )
    ref = RepoRef(owner=facts.owner, name=facts.name)
    analysis = Analysis(
        repo=ref,
        slug=ref.slug,
        cache_key=row.cache_key,
        facts=facts,
        scorecard=scorecard,
        narrative=narrative,
        provenance=Provenance(
            github_calls_used=row.github_calls_used,
            llm_status=row.llm_status,
            llm_model=row.llm_model,
            prompt_template_version=row.prompt_template_version,
            scorecard_version=row.scorecard_version,
            analysis_schema_version=row.analysis_schema_version,
            deprecation_table_version=facts.deprecation_table_version,
            duration_ms=row.duration_ms,
            data_gaps=[],  # derived by Analysis.merged_data_gaps()
            fixture_source=fixture_source,
        ),
        created_at=datetime.fromisoformat(row.created_at),
    )
    return analysis
