"""SQLite connection factory and migration runner (R-2, R-18.5, E3).

Three decisions worth stating:

  - **Per-thread connections.** The Flask app runs analyses on a bounded worker
    pool, so a connection is never shared between threads. `sqlite3` objects are
    cheap; a thread-local is simpler and safer than a pool.
  - **Autocommit plus explicit transactions.** `isolation_level=None` disables
    the stdlib's implicit transaction handling, so `BEGIN IMMEDIATE` in
    `transaction()` is the only place a write transaction starts. That is what
    makes the single-transaction cache write in design 4.6 mean what it says.
  - **WAL.** A reader (gallery render) never blocks on the writer (an analysis
    worker), which is what keeps the cache-hit budget in NFR-1 achievable.

Nothing here is importable from `necropsy/scoring/` or `necropsy/analysis/`;
those packages must stay pure (E6).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from itertools import count
from pathlib import Path

log = logging.getLogger("necropsy.store")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_MEMORY_ALIASES = frozenset({":memory:", "", "memory"})
_memory_counter = count(1)


def utc_now_iso() -> str:
    """Current UTC instant, ISO 8601, second precision. Row bookkeeping only.

    The analysis clock is `as_of`, frozen at fetch time and carried in the
    facts. Nothing derived from this function may reach the scorer (E6).
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """Every `NNNN_name.sql` in order. A non-conforming filename is a defect."""
    found: list[tuple[int, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        prefix = path.name.split("_", 1)[0]
        if not prefix.isdigit():
            raise ValueError(f"migration filename must start with digits: {path.name}")
        found.append((int(prefix), path))
    versions = [v for v, _ in found]
    if len(set(versions)) != len(versions):
        raise ValueError(f"duplicate migration version in {directory}")
    return found


class Database:
    """One SQLite file (or one shared in-memory database) and its connections."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        raw = str(path).strip()
        self.in_memory = raw in _MEMORY_ALIASES
        if self.in_memory:
            # A named shared-cache memory database, so per-thread connections
            # still see the same tables. Tests rely on this.
            self.dsn = f"file:necropsy_mem_{next(_memory_counter)}?mode=memory&cache=shared"
            self._uri = True
        else:
            resolved = Path(raw).expanduser()
            if resolved.parent and not resolved.parent.exists():
                resolved.parent.mkdir(parents=True, exist_ok=True)
            self.dsn = str(resolved)
            self._uri = False

        self._local = threading.local()
        self._lock = threading.Lock()
        self._connections: list[sqlite3.Connection] = []
        # A shared-cache memory database is dropped when its last connection
        # closes, so hold one open for the lifetime of the Database.
        self._anchor = self._open() if self.in_memory else None

    # -- connections --------------------------------------------------------
    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.dsn,
            uri=self._uri,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        if not self.in_memory:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
        with self._lock:
            self._connections.append(conn)
        return conn

    def connect(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._open()
            self._local.conn = conn
            self._local.depth = 0
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A write transaction, re-entrant so callers can compose safely.

        Only the outermost scope issues BEGIN/COMMIT; an inner scope joins the
        one already open. That is what lets `save_analysis` be called either
        standalone or inside the larger cache-write transaction.
        """
        conn = self.connect()
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield conn
            finally:
                self._local.depth = depth
            return

        conn.execute("BEGIN IMMEDIATE")
        self._local.depth = 1
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        finally:
            self._local.depth = 0

    def close(self) -> None:
        """Close every connection this Database opened, including the anchor."""
        with self._lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover - close is best effort
                log.debug("ignoring error while closing a sqlite connection")
        self._local = threading.local()
        self._anchor = None

    # -- migrations ---------------------------------------------------------
    def applied_versions(self) -> set[int]:
        conn = self.connect()
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        if not exists:
            return set()
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {int(r["version"]) for r in rows}

    def migrate(self, directory: Path = MIGRATIONS_DIR) -> list[int]:
        """Apply pending migrations in order. Returns the versions applied.

        Safe to call on every startup and in every process: an already-recorded
        version is skipped, so a second call applies nothing and returns [].
        """
        conn = self.connect()
        applied = self.applied_versions()
        newly: list[int] = []
        for version, path in discover_migrations(directory):
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, utc_now_iso()),
            )
            newly.append(version)
            log.info("applied migration %04d (%s)", version, path.name)
        return newly


def open_database(path: str | Path, *, migrate: bool = True) -> Database:
    """Open (and by default migrate) a database. The one constructor callers need."""
    db = Database(path)
    if migrate:
        db.migrate()
    return db
