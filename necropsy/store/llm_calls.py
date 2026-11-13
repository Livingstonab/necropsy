"""LLM accounting (R-15.4, E9).

Every attempt is recorded, including the ones that were rejected, because "the
model produced invalid JSON twice" is exactly the kind of thing that is invisible
unless it is written down. The prompt hash and template version make a call
replayable; the outcome makes the fallback rate measurable.

No prompt text and no completion text is stored: the hash identifies the prompt,
and the accepted narrative is already persisted on the analysis row. Storing the
prompt body would mean storing repository content twice for no new information.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Protocol

from necropsy.store.db import utc_now_iso


class AttemptLike(Protocol):
    """The subset of `llm.validate.Attempt` this table needs.

    Read-only properties rather than plain attributes, so a frozen dataclass
    satisfies it. A writable protocol attribute would demand a mutable field and
    this layer has no business writing to the record it is persisting.
    """

    @property
    def attempt(self) -> int: ...

    @property
    def outcome(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def prompt_hash(self) -> str: ...

    @property
    def prompt_template_version(self) -> int: ...

    @property
    def tokens_in(self) -> int | None: ...

    @property
    def tokens_out(self) -> int | None: ...

    @property
    def latency_ms(self) -> int | None: ...


def record_attempts(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
    attempts: Iterable[AttemptLike],
    analysis_id: int | None = None,
) -> int:
    """Insert one row per attempt. Returns how many were written."""
    now = utc_now_iso()
    written = 0
    for attempt in attempts:
        conn.execute(
            """
            INSERT INTO llm_calls (
                analysis_id, cache_key, model, prompt_template_version, prompt_hash,
                tokens_in, tokens_out, latency_ms, outcome, attempt, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis_id,
                cache_key,
                attempt.model,
                attempt.prompt_template_version,
                attempt.prompt_hash,
                attempt.tokens_in,
                attempt.tokens_out,
                attempt.latency_ms,
                attempt.outcome,
                attempt.attempt,
                now,
            ),
        )
        written += 1
    return written


def count_for_cache_key(conn: sqlite3.Connection, cache_key: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM llm_calls WHERE cache_key = ?", (cache_key,)
    ).fetchone()
    return int(row["n"])


def outcomes_for_cache_key(conn: sqlite3.Connection, cache_key: str) -> list[str]:
    """Attempt outcomes in order, which is how a fallback is explained after the fact."""
    rows = conn.execute(
        "SELECT outcome FROM llm_calls WHERE cache_key = ? ORDER BY attempt ASC, id ASC",
        (cache_key,),
    ).fetchall()
    return [str(r["outcome"]) for r in rows]
