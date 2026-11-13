"""Rate-limit ledger (R-14.2, R-14.8, E2).

Every GitHub response's rate-limit headers are recorded here. The point is that
the remaining budget is *observed*, never guessed: the pre-analysis floor check
and the header indicator both read this table.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from necropsy.store.db import utc_now_iso


@dataclass(frozen=True)
class RateLimitObservation:
    """One response's rate-limit headers, already parsed."""

    endpoint: str
    status: int
    limit: int | None = None
    remaining: int | None = None
    used: int | None = None
    reset_at: datetime | None = None
    token_present: bool = False
    observed_at: str | None = None


@dataclass(frozen=True)
class RateLimitState:
    """The most recent observation, which is what the floor check consults."""

    observed_at: str
    endpoint: str
    status: int
    limit: int | None
    remaining: int | None
    used: int | None
    reset_at: str | None
    token_present: bool

    @property
    def reset_datetime(self) -> datetime | None:
        return datetime.fromisoformat(self.reset_at) if self.reset_at else None

    def retry_after_seconds(self, *, now: datetime | None = None) -> int | None:
        """Whole seconds until the window resets, floored at zero."""
        reset = self.reset_datetime
        if reset is None:
            return None
        current = now or datetime.now(UTC)
        return max(0, int((reset - current).total_seconds()))

    def below_floor(self, floor: int) -> bool:
        """True only when a measurement exists and it is under the floor.

        An absent measurement is not evidence of exhaustion, so it does not
        refuse the analysis; the budget counter in GitHubClient still caps it.
        """
        return self.remaining is not None and self.remaining < floor


def record(conn: sqlite3.Connection, obs: RateLimitObservation) -> None:
    conn.execute(
        """
        INSERT INTO rate_limit_ledger (
            observed_at, endpoint, status, rl_limit, rl_remaining, rl_used,
            rl_reset_at, token_present
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            obs.observed_at or utc_now_iso(),
            obs.endpoint,
            obs.status,
            obs.limit,
            obs.remaining,
            obs.used,
            obs.reset_at.isoformat() if obs.reset_at else None,
            int(obs.token_present),
        ),
    )


def latest(conn: sqlite3.Connection) -> RateLimitState | None:
    """Most recent observation, or None when no GitHub call has been made yet."""
    row = conn.execute(
        """
        SELECT observed_at, endpoint, status, rl_limit, rl_remaining, rl_used,
               rl_reset_at, token_present
          FROM rate_limit_ledger
         ORDER BY observed_at DESC, id DESC
         LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return RateLimitState(
        observed_at=row["observed_at"],
        endpoint=row["endpoint"],
        status=int(row["status"]),
        limit=row["rl_limit"],
        remaining=row["rl_remaining"],
        used=row["rl_used"],
        reset_at=row["rl_reset_at"],
        token_present=bool(row["token_present"]),
    )


def count_observations(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM rate_limit_ledger").fetchone()["n"])


# -- ETag store (R-14.7) ---------------------------------------------------
def get_etag(conn: sqlite3.Connection, endpoint_key: str) -> tuple[str, str] | None:
    """`(etag, body_json)` for a conditional request, or None."""
    row = conn.execute(
        "SELECT etag, body_json FROM etags WHERE endpoint_key = ?", (endpoint_key,)
    ).fetchone()
    return (row["etag"], row["body_json"]) if row else None


def put_etag(conn: sqlite3.Connection, endpoint_key: str, etag: str, body_json: str) -> None:
    conn.execute(
        """
        INSERT INTO etags (endpoint_key, etag, body_json, stored_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(endpoint_key) DO UPDATE SET
            etag = excluded.etag,
            body_json = excluded.body_json,
            stored_at = excluded.stored_at
        """,
        (endpoint_key, etag, body_json, utc_now_iso()),
    )
