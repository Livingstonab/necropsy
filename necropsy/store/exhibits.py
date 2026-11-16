"""Exhibits: the presentation record, one per repository (R-5, R-6).

The gallery is a single indexed read. `list_exhibits` joins the exhibit row to
its analysis and rebuilds each `Analysis` from the JSON columns in that same
query, so rendering eight cards costs one query rather than nine (NFR-1).

Sorting is server-side and closed to three options (P8). `deadest` orders by
how long the repository has been silent; `hardest` orders by revival score.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, get_args

from necropsy.models.analysis import Analysis
from necropsy.store.analyses import AnalysisRow, load_analysis
from necropsy.store.db import utc_now_iso

Sort = Literal["curated", "deadest", "hardest"]
SORTS: tuple[str, ...] = get_args(Sort)

# Whitelisted, never interpolated from user input (E4).
_ORDER_BY: dict[str, str] = {
    "curated": "e.is_demo DESC, e.display_order ASC, e.slug ASC",
    "deadest": "a.last_commit_at ASC, a.score DESC, e.slug ASC",
    "hardest": "a.score DESC, a.last_commit_at ASC, e.slug ASC",
}

_SELECT = """
SELECT e.id            AS exhibit_id,
       e.slug          AS slug,
       e.plaque_title  AS plaque_title,
       e.is_demo       AS is_demo,
       e.curated       AS curated,
       e.curator_note  AS curator_note,
       e.display_order AS display_order,
       e.user_inscription AS user_inscription,
       e.inscribed_at  AS inscribed_at,
       e.updated_at    AS updated_at,
       a.*
  FROM exhibits e
  JOIN analyses a ON a.id = e.analysis_id
"""


@dataclass(frozen=True)
class Exhibit:
    """An exhibit and the analysis behind it, ready to render."""

    exhibit_id: int
    slug: str
    plaque_title: str
    is_demo: bool
    curated: bool
    curator_note: str | None
    display_order: int
    updated_at: str
    row: AnalysisRow
    analysis: Analysis
    user_inscription: str | None = None
    inscribed_at: str | None = None

    @classmethod
    def from_sqlite(cls, row: sqlite3.Row) -> Exhibit:
        analysis_row = AnalysisRow.from_sqlite(row)
        keys = row.keys()
        return cls(
            exhibit_id=int(row["exhibit_id"]),
            slug=row["slug"],
            plaque_title=row["plaque_title"],
            is_demo=bool(row["is_demo"]),
            curated=bool(row["curated"]),
            curator_note=row["curator_note"],
            display_order=int(row["display_order"]),
            updated_at=row["updated_at"],
            row=analysis_row,
            analysis=load_analysis(analysis_row),
            user_inscription=row["user_inscription"] if "user_inscription" in keys else None,
            inscribed_at=row["inscribed_at"] if "inscribed_at" in keys else None,
        )


def upsert_exhibit(
    conn: sqlite3.Connection,
    *,
    repo_id: int,
    analysis_id: int,
    slug: str,
    plaque_title: str,
    is_demo: bool = False,
    curated: bool = False,
    curator_note: str | None = None,
    display_order: int = 1000,
) -> int:
    """Point the repository's exhibit at `analysis_id`, creating it if needed.

    Curation flags and the curator note are only overwritten when the caller
    supplies them, so re-seeding cannot silently demote a curated exhibit and
    an ordinary re-analysis cannot promote one (R-5.5).
    """
    conn.execute(
        """
        INSERT INTO exhibits (
            repo_id, analysis_id, slug, plaque_title,
            is_demo, curated, curator_note, display_order, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_id) DO UPDATE SET
            analysis_id   = excluded.analysis_id,
            plaque_title  = excluded.plaque_title,
            is_demo       = MAX(exhibits.is_demo, excluded.is_demo),
            curated       = MAX(exhibits.curated, excluded.curated),
            curator_note  = COALESCE(excluded.curator_note, exhibits.curator_note),
            display_order = excluded.display_order,
            updated_at    = excluded.updated_at
        """,
        (
            repo_id,
            analysis_id,
            slug,
            plaque_title,
            int(is_demo),
            int(curated),
            curator_note,
            display_order,
            utc_now_iso(),
        ),
    )
    row = conn.execute("SELECT id FROM exhibits WHERE repo_id = ?", (repo_id,)).fetchone()
    if row is None:  # pragma: no cover - the upsert above guarantees a row
        raise sqlite3.IntegrityError(f"exhibit row missing for repo {repo_id}")
    return int(row["id"])


#: The max length an inscription renders at; the full line is stored (R edge case).
INSCRIPTION_DISPLAY_MAX = 100


def remove_absent_demos(conn: sqlite3.Connection, keep_slugs: set[str]) -> int:
    """Take off the wall any seeded exhibit the manifest no longer declares.

    The manifest is the declaration of the curated wall (R-5.3), so removing an
    entry from it has to remove the exhibit too — otherwise re-seeding leaves a
    repo on the wall that nothing vouches for any more.

    The exhibit row goes, the analysis does not: it stays as evidence and as the
    cache entry a re-add would reuse. Clearing `is_demo` instead of deleting is
    not enough, because a visitor's own analysis is also a non-demo exhibit — the
    wall would keep showing a retired specimen beside real ones, indistinguishable
    from something a visitor asked for. Only rows the seeder marks (`is_demo` or
    `curated`) are eligible, so a visitor's plaque can never be removed by this.
    """
    if not keep_slugs:
        return 0
    placeholders = ", ".join("?" for _ in keep_slugs)
    cursor = conn.execute(
        f"""
        DELETE FROM exhibits
        WHERE (is_demo = 1 OR curated = 1) AND slug NOT IN ({placeholders})
        """,
        tuple(sorted(keep_slugs)),
    )
    return cursor.rowcount


def set_inscription(conn: sqlite3.Connection, slug: str, text: str | None) -> bool:
    """Carve (or clear) a visitor's inscription on an exhibit. Returns True if a
    row was updated. An empty or whitespace-only line clears it rather than
    storing nothing meaningful (R edge case: empty inscription is a no-op)."""
    cleaned = (text or "").strip()
    value = cleaned or None
    now = utc_now_iso() if value else None
    cur = conn.execute(
        "UPDATE exhibits SET user_inscription = ?, inscribed_at = ? WHERE slug = ?",
        (value, now, slug.lower()),
    )
    return cur.rowcount > 0


def get_inscription(conn: sqlite3.Connection, slug: str) -> str | None:
    row = conn.execute(
        "SELECT user_inscription FROM exhibits WHERE slug = ?", (slug.lower(),)
    ).fetchone()
    return row["user_inscription"] if row else None


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Exhibit | None:
    """Gallery navigation: zero network calls, one indexed read (R-2.5)."""
    row = conn.execute(f"{_SELECT} WHERE e.slug = ?", (slug.lower(),)).fetchone()
    return Exhibit.from_sqlite(row) if row else None


def get_by_canonical_url(conn: sqlite3.Connection, canonical_url: str) -> Exhibit | None:
    row = conn.execute(f"{_SELECT} WHERE a.canonical_url = ?", (canonical_url.lower(),)).fetchone()
    return Exhibit.from_sqlite(row) if row else None


def list_exhibits(
    conn: sqlite3.Connection,
    *,
    sort: str = "curated",
    limit: int = 50,
    offset: int = 0,
    demo_only: bool = False,
) -> list[Exhibit]:
    order_by = _ORDER_BY.get(sort)
    if order_by is None:
        raise ValueError(f"sort must be one of {SORTS}, got {sort!r}")
    where = " WHERE e.is_demo = 1" if demo_only else ""
    rows = conn.execute(
        f"{_SELECT}{where} ORDER BY {order_by} LIMIT ? OFFSET ?",
        (max(0, limit), max(0, offset)),
    ).fetchall()
    return [Exhibit.from_sqlite(r) for r in rows]


def count_exhibits(conn: sqlite3.Connection, *, demo_only: bool = False) -> int:
    sql = "SELECT COUNT(*) AS n FROM exhibits"
    if demo_only:
        sql += " WHERE is_demo = 1"
    return int(conn.execute(sql).fetchone()["n"])
