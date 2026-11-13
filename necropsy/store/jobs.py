"""Jobs: the handle a browser polls while a cache-miss analysis runs (R-1.5, E14).

Cache-miss work never runs on a request thread, so the client needs something
to ask about. A job row is that something: a state, a result pointer, and an
error code from the closed taxonomy.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from typing import Literal

from necropsy.errors import ErrorCode
from necropsy.store.db import utc_now_iso

JobState = Literal["queued", "running", "done", "failed"]


@dataclass(frozen=True)
class Job:
    job_id: str
    canonical_url: str
    cache_key: str | None
    state: str
    result_analysis_id: int | None
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str

    @property
    def finished(self) -> bool:
        return self.state in {"done", "failed"}

    @classmethod
    def from_sqlite(cls, row: sqlite3.Row) -> Job:
        return cls(
            job_id=row["job_id"],
            canonical_url=row["canonical_url"],
            cache_key=row["cache_key"],
            state=row["state"],
            result_analysis_id=(
                int(row["result_analysis_id"]) if row["result_analysis_id"] is not None else None
            ),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def new_job_id() -> str:
    return uuid.uuid4().hex


def create(
    conn: sqlite3.Connection,
    canonical_url: str,
    *,
    job_id: str | None = None,
    cache_key: str | None = None,
) -> Job:
    identifier = job_id or new_job_id()
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO jobs (job_id, canonical_url, cache_key, state, created_at, updated_at)
        VALUES (?, ?, ?, 'queued', ?, ?)
        """,
        (identifier, canonical_url, cache_key, now, now),
    )
    created = get(conn, identifier)
    if created is None:  # pragma: no cover - the insert above guarantees a row
        raise sqlite3.IntegrityError(f"job row missing for {identifier}")
    return created


def get(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return Job.from_sqlite(row) if row else None


def mark_running(conn: sqlite3.Connection, job_id: str, *, cache_key: str | None = None) -> None:
    conn.execute(
        """
        UPDATE jobs
           SET state = 'running',
               cache_key = COALESCE(?, cache_key),
               updated_at = ?
         WHERE job_id = ?
        """,
        (cache_key, utc_now_iso(), job_id),
    )


def mark_done(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    analysis_id: int,
    cache_key: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE jobs
           SET state = 'done',
               result_analysis_id = ?,
               cache_key = COALESCE(?, cache_key),
               error_code = NULL,
               error_message = NULL,
               updated_at = ?
         WHERE job_id = ?
        """,
        (analysis_id, cache_key, utc_now_iso(), job_id),
    )


def mark_failed(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    code: ErrorCode | str,
    message: str,
) -> None:
    """Record the failure. The error state is the only thing a failed analysis
    persists; no partial success is ever written (E3)."""
    conn.execute(
        """
        UPDATE jobs
           SET state = 'failed',
               error_code = ?,
               error_message = ?,
               updated_at = ?
         WHERE job_id = ?
        """,
        (str(code), message, utc_now_iso(), job_id),
    )
