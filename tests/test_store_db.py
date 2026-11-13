"""T-1.3: migrations, connections, transactions (R-2, R-18.5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from necropsy.store.db import Database, discover_migrations, open_database

EXPECTED_TABLES = {
    "schema_migrations",
    "repos",
    "analyses",
    "exhibits",
    "manifest_entries",
    "rate_limit_ledger",
    "etags",
    "llm_calls",
    "jobs",
}


def _tables(db: Database) -> set[str]:
    rows = db.connect().execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {r["name"] for r in rows}


def test_migration_files_are_ordered_and_unique() -> None:
    found = discover_migrations()
    assert found, "at least 0001_init.sql must exist"
    versions = [v for v, _ in found]
    assert versions == sorted(versions)
    assert versions[0] == 1


def test_migrate_creates_every_table(db: Database) -> None:
    assert _tables(db) >= EXPECTED_TABLES


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    """Done-criteria: migrations apply twice with no error and no re-application."""
    db = open_database(tmp_path / "twice.db")
    try:
        applied = db.applied_versions()
        assert 1 in applied, "the initial migration must be applied"
        assert db.migrate() == [], "a second migrate() must apply nothing"
        assert db.applied_versions() == applied, "the set must not change on re-run"
        assert _tables(db) >= EXPECTED_TABLES
    finally:
        db.close()


def test_startup_pragmas_are_set(tmp_path: Path) -> None:
    db = open_database(tmp_path / "pragmas.db")
    try:
        conn = db.connect()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        db.close()


def test_foreign_keys_are_enforced(db: Database) -> None:
    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            """
                INSERT INTO exhibits (repo_id, analysis_id, slug, plaque_title, updated_at)
                VALUES (999, 999, 'nope--nope', 'Nope', '2026-01-01T00:00:00+00:00')
                """
        )


def test_transaction_rolls_back_on_error(db: Database) -> None:
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO repos (canonical_url, owner, name, html_url, first_seen_at) "
            "VALUES ('github.com/a/b', 'a', 'b', 'https://github.com/a/b', 'now')"
        )
        raise RuntimeError("abort")
    assert db.connect().execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"] == 0


def test_transaction_is_reentrant(db: Database) -> None:
    with db.transaction() as outer:
        outer.execute(
            "INSERT INTO repos (canonical_url, owner, name, html_url, first_seen_at) "
            "VALUES ('github.com/a/b', 'a', 'b', 'https://github.com/a/b', 'now')"
        )
        with db.transaction() as inner:
            inner.execute(
                "INSERT INTO repos (canonical_url, owner, name, html_url, first_seen_at) "
                "VALUES ('github.com/c/d', 'c', 'd', 'https://github.com/c/d', 'now')"
            )
    assert db.connect().execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"] == 2


def test_each_thread_gets_its_own_connection_on_the_same_database(db: Database) -> None:
    import threading

    seen: list[int] = []
    identities: set[int] = set()

    def worker() -> None:
        conn = db.connect()
        identities.add(id(conn))
        seen.append(conn.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"])

    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO repos (canonical_url, owner, name, html_url, first_seen_at) "
            "VALUES ('github.com/a/b', 'a', 'b', 'https://github.com/a/b', 'now')"
        )

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert seen == [1, 1, 1], "every thread must see the same in-memory database"
    assert len(identities) == 3, "connections must not be shared between threads"


def test_in_memory_databases_are_isolated_from_each_other() -> None:
    first, second = open_database(":memory:"), open_database(":memory:")
    try:
        with first.transaction() as conn:
            conn.execute(
                "INSERT INTO repos (canonical_url, owner, name, html_url, first_seen_at) "
                "VALUES ('github.com/a/b', 'a', 'b', 'https://github.com/a/b', 'now')"
            )
        assert second.connect().execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"] == 0
    finally:
        first.close()
        second.close()
