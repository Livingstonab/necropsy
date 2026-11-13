"""Shared fixtures. No network, no API keys, anywhere in this suite (E11)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from necropsy.store.db import Database, open_database

_SECRET_ENV = (
    "GITHUB_TOKEN",
    "LLM_API_KEY",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "DATABASE_PATH",
    "LOG_LEVEL",
    "PORT",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts from an empty environment, which is also the claim in
    E12 that the app runs with nothing set.

    `load_dotenv` is also neutralized: a developer with a real `.env` in the
    project root must not have its token bleed into a suite that asserts the app
    behaves with nothing configured. The suite is hermetic regardless of the
    machine it runs on.
    """
    for name in _SECRET_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("necropsy.config.load_dotenv", lambda path=".env": None)


@pytest.fixture
def db() -> Iterator[Database]:
    """A migrated, shared-cache in-memory database visible to every thread."""
    database = open_database(":memory:")
    try:
        yield database
    finally:
        database.close()


@pytest.fixture
def file_db(tmp_path: Path) -> Iterator[Database]:
    """A migrated on-disk database, for anything that must survive a reconnect."""
    database = open_database(tmp_path / "necropsy.db")
    try:
        yield database
    finally:
        database.close()
