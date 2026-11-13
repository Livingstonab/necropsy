"""The tombstone inscription: store, route, persistence, and the offline rule.

The inscription is the one visitor-authored field. These tests cover that it
stores and reads back, that an empty line is a no-op, that it persists across a
reload, and — the offline rule — that a cached exhibit renders fully, letter and
all, with the network mocked to raise on any call.
"""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from necropsy.config import Config
from necropsy.graveyard import seed
from necropsy.store import exhibits
from necropsy.store.db import Database
from necropsy.web.app import create_app

GEMSTONE = "necropsy-specimens--gemstone-cart"


@pytest.fixture
def seeded(db: Database) -> Database:
    seed(db)
    return db


@pytest.fixture
def client(seeded: Database) -> FlaskClient:
    app = create_app(db=seeded, config=Config())
    app.testing = True
    return app.test_client()


# -- the store -------------------------------------------------------------
def test_set_and_get_inscription(seeded: Database) -> None:
    with seeded.transaction() as conn:
        updated = exhibits.set_inscription(conn, GEMSTONE, "I loved you. I'm sorry.")
    assert updated is True
    assert exhibits.get_inscription(seeded.connect(), GEMSTONE) == "I loved you. I'm sorry."


def test_empty_inscription_clears_rather_than_stores_blank(seeded: Database) -> None:
    with seeded.transaction() as conn:
        exhibits.set_inscription(conn, GEMSTONE, "something")
    with seeded.transaction() as conn:
        exhibits.set_inscription(conn, GEMSTONE, "   ")
    assert exhibits.get_inscription(seeded.connect(), GEMSTONE) is None


def test_inscription_on_an_unknown_slug_updates_nothing(seeded: Database) -> None:
    with seeded.transaction() as conn:
        assert exhibits.set_inscription(conn, "no--such", "x") is False


def test_inscription_rides_on_the_exhibit_not_the_analysis(seeded: Database) -> None:
    """E3: the cached analysis stays byte-identical; the inscription is separate."""
    before = exhibits.get_by_slug(seeded.connect(), GEMSTONE)
    assert before is not None
    before_json = before.row.normalized_json
    with seeded.transaction() as conn:
        exhibits.set_inscription(conn, GEMSTONE, "a line")
    after = exhibits.get_by_slug(seeded.connect(), GEMSTONE)
    assert after is not None
    assert after.row.normalized_json == before_json  # analysis untouched
    assert after.user_inscription == "a line"


# -- the route and persistence ---------------------------------------------
def test_inscribe_route_persists_across_reload(client: FlaskClient) -> None:
    res = client.post(
        f"/api/exhibit/{GEMSTONE}/inscribe",
        json={"inscription": "I loved you. I'm sorry."},
    )
    assert res.status_code == 200
    assert res.get_json()["inscription"] == "I loved you. I'm sorry."

    # Reload the exhibit page: the inscription is rendered in place.
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "I loved you. I&#39;m sorry." in body or "I loved you. I'm sorry." in body
    assert "— you" in body


def test_inscribe_empty_is_a_noop_via_route(client: FlaskClient) -> None:
    client.post(f"/api/exhibit/{GEMSTONE}/inscribe", json={"inscription": "x"})
    res = client.post(f"/api/exhibit/{GEMSTONE}/inscribe", json={"inscription": ""})
    assert res.status_code == 200
    assert res.get_json()["inscription"] is None


def test_inscribe_unknown_exhibit_is_in_character_404(client: FlaskClient) -> None:
    res = client.post("/api/exhibit/no--such/inscribe", json={"inscription": "x"})
    assert res.status_code == 404


def test_overlong_inscription_is_stored_truncated_to_limit(client: FlaskClient) -> None:
    long_line = "x" * 400
    res = client.post(f"/api/exhibit/{GEMSTONE}/inscribe", json={"inscription": long_line})
    assert res.status_code == 200
    # Stored up to the route cap (500); display truncation is a template concern.
    stored = res.get_json()["inscription"]
    assert stored is not None and len(stored) <= 500


# -- the letter renders on the plaque --------------------------------------
def test_exhibit_renders_the_letter(client: FlaskClient) -> None:
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "The letter it never sent" in body
    assert "letter-body" in body


def test_exhibit_shows_the_tombstone_and_share(client: FlaskClient) -> None:
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "tombstone" in body
    assert "Inscribe a line for this repo" in body
    assert "Share this tombstone" in body


def test_cached_tag_is_shown(client: FlaskClient) -> None:
    """The offline rule: proof-of-real-data tag is visible, not hidden."""
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "cached-tag" in body
    # Specimens say so plainly; real repos would link to the commit.
    assert "specimen" in body.lower() or "Cached from GitHub" in body


# -- the offline rule: a cached exhibit renders with zero network calls -----
def test_cached_exhibit_renders_offline(seeded: Database, monkeypatch) -> None:
    """Integration: mock the network to raise, then render a cached exhibit.

    Nothing on the exhibit path should touch httpx. If it did, the patched client
    would raise and this test would fail, which is exactly the guarantee (R-2.5,
    Property 11).
    """
    import httpx

    def _boom(*args, **kwargs):
        raise AssertionError("the cached exhibit path must make no network call")

    monkeypatch.setattr(httpx.Client, "get", _boom)
    monkeypatch.setattr(httpx.Client, "post", _boom)
    monkeypatch.setattr(httpx.Client, "request", _boom)

    app = create_app(db=seeded, config=Config())
    app.testing = True
    client = app.test_client()

    res = client.get(f"/exhibit/{GEMSTONE}")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    # The full plaque, including the letter, rendered from cache.
    assert "The letter it never sent" in body
    assert "Revival difficulty" in body
    assert "Vital signs" in body
