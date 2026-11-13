"""The museum web app, offline (R-1, R-6, R-7, R-13, E8).

Driven through Flask's test client against a seeded in-memory database. No
network: the gallery and every exhibit are cache reads, which is the whole point
of the demo path (P4). The one route that could reach the network — the analyze
job — is covered for its enqueue-and-envelope behaviour, not a live fetch.

A few things these pin down that a human clicking around would check first: the
gallery renders a card per exhibit and no raw repo string escapes unescaped; an
exhibit shows all seven sections in order; and an error renders inside the
gallery frame as a coroner's sentence, never a stack trace (R-13.9).
"""

from __future__ import annotations

import pytest
from flask.testing import FlaskClient

from necropsy.config import Config
from necropsy.graveyard import seed
from necropsy.store.db import Database
from necropsy.web.app import create_app

GEMSTONE = "necropsy-specimens--gemstone-cart"


@pytest.fixture
def client(db: Database) -> FlaskClient:
    seed(db)
    app = create_app(db=db, config=Config())
    app.testing = True
    return app.test_client()


# -- gallery ---------------------------------------------------------------
def test_gallery_renders_one_card_per_exhibit(client: FlaskClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "graveyard" in body.lower()
    # Eight seeded specimens, eight cards.
    assert body.count("exhibit-card") >= 8
    assert "Paste a GitHub URL" in body


def test_gallery_has_one_input_and_no_login(client: FlaskClient) -> None:
    """R-1.1: one field, a submit control, no account wall."""
    body = client.get("/").get_data(as_text=True)
    assert body.count('name="url"') == 1
    for forbidden in ("password", "sign in", "log in", "cookie", "consent"):
        assert forbidden not in body.lower(), forbidden


def test_gallery_sort_is_server_side(client: FlaskClient) -> None:
    hardest = client.get("/?sort=hardest").get_data(as_text=True)
    # monolith-erp (97) should appear before closing-time (18) in the markup.
    assert hardest.index("monolith-erp") < hardest.index("closing-time")


def test_unknown_sort_falls_back_to_curated(client: FlaskClient) -> None:
    assert client.get("/?sort=nonsense").status_code == 200


# -- exhibit ---------------------------------------------------------------
def test_exhibit_renders_all_seven_sections_in_order(client: FlaskClient) -> None:
    """R-7.1: the plaque carries every section, in a sensible reading order.

    The upgraded plaque folds 'what was built' and 'time of death' into the
    Vital signs panel (what the repo is and how strong it is), then cause,
    ambition, scorecard, missing, last words, plan.
    """
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert body.count("<h1") >= 1  # the epitaph
    for heading in (
        "Vital signs",
        "Cause of death",
        "Ambition vs reality",
        "Revival difficulty",
        "What's missing",
        "Resurrection plan",
    ):
        assert heading in body, heading
    assert body.index("Vital signs") < body.index("Resurrection plan")
    # The died-on line still carries the time of death.
    assert "days before capture" in body


def test_exhibit_shows_the_full_scorecard(client: FlaskClient) -> None:
    """E5: every factor visible with its contribution, plus the checksum."""
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "Abandonment" in body and "Dependency rot" in body
    assert "Sum of contributions" in body
    assert "weights sha256:" in body


def test_exhibit_links_to_github_and_shows_as_of(client: FlaskClient) -> None:
    """R-7.4: a link out and the capture timestamp."""
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "https://github.com/necropsy-specimens/gemstone-cart" in body
    assert "as of" in body


def test_missing_exhibit_is_in_character_not_a_stack_trace(client: FlaskClient) -> None:
    """R-13.9: an unknown slug renders the error frame, not a traceback."""
    res = client.get("/exhibit/no-such-thing")
    assert res.status_code == 404
    body = res.get_data(as_text=True)
    assert "NOT_IN_GRAVEYARD" in body
    assert "Traceback" not in body
    assert "Return to the graveyard" in body


# -- analyze flow ----------------------------------------------------------
def test_bad_url_is_rejected_before_any_job(client: FlaskClient) -> None:
    """R-4.4: garbage input is an envelope, and no job is enqueued."""
    res = client.post("/api/analyze", json={"url": "this is not a url"})
    assert res.status_code in (400, 422)
    body = res.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] in {"INVALID_URL", "UNSUPPORTED_HOST"}


def test_unsupported_host_is_rejected(client: FlaskClient) -> None:
    res = client.post("/api/analyze", json={"url": "https://gitlab.com/a/b"})
    assert res.status_code == 400
    assert res.get_json()["error"]["code"] == "UNSUPPORTED_HOST"


# -- health ----------------------------------------------------------------
def test_health_reports_counts_and_token_boolean(client: FlaskClient) -> None:
    """R-14.8, E12: token_present is a boolean, never the token."""
    body = client.get("/api/health").get_json()
    assert body["ok"] is True
    assert body["exhibits"] >= 8
    assert body["demo_exhibits"] >= 8
    assert body["token_present"] is False  # clean_env fixture
    assert isinstance(body["token_present"], bool)


def test_health_never_leaks_a_secret(client: FlaskClient) -> None:
    raw = client.get("/api/health").get_data(as_text=True)
    assert "ghp_" not in raw and "sk-" not in raw


# -- the upgraded plaque ---------------------------------------------------
def test_exhibit_shows_vital_signs(client: FlaskClient) -> None:
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "Vital signs" in body
    assert "health-strip" in body
    assert "structural supports present" in body


def test_exhibit_shows_ambition_vs_reality(client: FlaskClient) -> None:
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "Ambition vs reality" in body
    assert "Promised" in body and "Delivered" in body
    assert "where most side projects die" in body


def test_exhibit_embeds_raw_evidence_receipts(client: FlaskClient) -> None:
    """The trust moment: a receipt carries raw GitHub JSON for a cited fact."""
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert 'class="receipt"' in body
    assert "Raw evidence" in body
    assert 'data-evidence-id=' in body


def test_exhibit_has_the_resurrect_button(client: FlaskClient) -> None:
    body = client.get(f"/exhibit/{GEMSTONE}").get_data(as_text=True)
    assert "Resurrect in Kiro" in body
    assert "Revival spec" in body  # the full spec is embedded for the clipboard


def test_kiro_setup_page_renders(client: FlaskClient) -> None:
    res = client.get("/kiro-setup")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "mcpServers" in body
    assert "write_revival_spec" in body


def test_gallery_marks_a_curator_pick(client: FlaskClient) -> None:
    body = client.get("/").get_data(as_text=True)
    assert body.count("Curator's pick") == 1
    assert "candle" in body  # flickering candles on the wall


# -- api exhibits ----------------------------------------------------------
def test_api_exhibits_returns_summaries(client: FlaskClient) -> None:
    body = client.get("/api/exhibits?sort=deadest").get_json()
    assert body["ok"] is True
    assert len(body["exhibits"]) >= 8
    first = body["exhibits"][0]
    assert {"slug", "repo_url", "score", "band", "epitaph"} <= set(first)


# -- escaping (E4 / R-16.3) ------------------------------------------------
def test_repo_text_is_escaped(db: Database) -> None:
    """A repository-derived string with HTML in it is escaped, never injected.

    Seeds one analysis whose description carries a script tag, then asserts the
    tag appears escaped in the rendered exhibit — Jinja2 autoescaping, with no
    `|safe` on a repo-derived value anywhere (E4).
    """
    from necropsy.store import exhibits
    from necropsy.store.analyses import save_analysis
    from tests import factories

    hostile = "<script>alert('xss')</script>"
    facts = factories.make_facts(owner="x", name="y", description=hostile)
    # Put the hostile string somewhere the plaque definitely renders: the summary.
    analysis = factories.make_analysis(owner="x", name="y", facts=facts)
    analysis = analysis.model_copy(
        update={
            "narrative": analysis.narrative.model_copy(
                update={"what_was_built_summary": f"A project {hostile} once."}
            )
        }
    )
    with db.transaction() as conn:
        row = save_analysis(conn, analysis, factories.make_raw_payloads("x", "y"))
        exhibits.upsert_exhibit(
            conn, repo_id=row.repo_id, analysis_id=row.id,
            slug=analysis.slug, plaque_title="x/y",
        )

    app = create_app(db=db, config=Config())
    app.testing = True
    body = app.test_client().get(f"/exhibit/{analysis.slug}").get_data(as_text=True)
    # The raw tag must never reach the page; Jinja2 escapes it to entities.
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body
