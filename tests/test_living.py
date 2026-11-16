"""An active repository is alive, not dead (P6): a check-up, never an autopsy.

The rule: committed to within LIVING_THRESHOLD_DAYS and not archived. What it
protects against is the worst moment a demo can have, which is a judge pasting
their own active repository and being told "I have waited 3 days. I am not
angry." So these tests pin the boundary, the guarantee that no model is called
for a living repo (E1), that nothing on a living page uses the vocabulary of
death, and that a living repo never hangs on the wall of the dead.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from flask.testing import FlaskClient
from hypothesis import given
from hypothesis import strategies as st

from necropsy import LIVING_THRESHOLD_DAYS
from necropsy.analysis.service import AnalysisService
from necropsy.analysis.vitals import pulse_line
from necropsy.config import Config
from necropsy.graveyard import seed, specimens
from necropsy.llm.client import RaisingLlmClient
from necropsy.llm.validate import living_narrative, narrate
from necropsy.mcp.server import NecropsyTools
from necropsy.models.scorecard import ScorecardInput
from necropsy.scoring import score
from necropsy.store import exhibits
from necropsy.store.db import Database
from necropsy.web.app import create_app
from tests import factories
from tests.test_service_pipeline import ScriptedGitHub

#: Words that must never appear on a living repository's page or narrative.
DEATH_VOCABULARY = ("abandon", "dead", "died", "death", "grave", "corpse", "exhume", "autopsy of")


def _facts(days: int, **overrides):
    """Facts for a repo last committed `days` ago, coherent end to end.

    The name and description are neutral on purpose: the factory's default repo is
    called "abandoned-cart", which would trip the death-vocabulary check for a
    reason that has nothing to do with the app's own wording.
    """
    base = {
        "days_since_last_commit": days,
        "last_commit_at": factories.AS_OF - timedelta(days=days),
        "description": "A small web shop.",
    }
    return factories.make_facts("octocat", "busy-cart", **{**base, **overrides})


# -- the rule --------------------------------------------------------------
def test_threshold_is_ten_days() -> None:
    assert LIVING_THRESHOLD_DAYS == 10


@pytest.mark.parametrize("days", [0, 1, 2, 3, 4, 7, 9])
def test_recently_committed_repos_are_living(days: int) -> None:
    assert _facts(days).is_living is True


@pytest.mark.parametrize("days", [10, 11, 30, 90, 400, 5000])
def test_quiet_repos_are_not_living(days: int) -> None:
    assert _facts(days).is_living is False


def test_an_archived_repo_is_never_living() -> None:
    """Archiving is the owner declaring it finished, however recent the commit."""
    assert _facts(2, is_archived=True).is_living is False


# -- no model is called, ever, for a living repo ---------------------------
@given(st.integers(min_value=0, max_value=LIVING_THRESHOLD_DAYS - 1))
def test_narrate_never_calls_the_provider_for_a_living_repo(days: int) -> None:
    """E1: a living repo must not spend a model call on prose that would be false.

    A client that raises on use proves it: if narrate() reached it, this fails.
    """
    facts = _facts(days)
    card = score(ScorecardInput.from_facts(facts))
    result = narrate(RaisingLlmClient(), facts, card)
    assert result.narrative.living is True
    assert result.calls_made == 0


# -- what a living repo is told --------------------------------------------
def test_living_narrative_withholds_every_deathly_section() -> None:
    narrative = living_narrative(_facts(2))
    assert narrative.living is True
    assert narrative.epitaph.source == "unavailable"
    assert narrative.final_letter.source == "unavailable"
    assert narrative.cause_of_death.source == "unavailable"
    assert narrative.resurrection_plan == []
    assert narrative.resurrection_plan_source == "unavailable"


@given(st.integers(min_value=0, max_value=LIVING_THRESHOLD_DAYS - 1))
def test_living_text_never_uses_the_vocabulary_of_death(days: int) -> None:
    """P6: dead is a diagnosis, and this repo has not been diagnosed."""
    narrative = living_narrative(_facts(days))
    words = " ".join(
        [
            narrative.what_was_built_summary,
            narrative.cause_of_death.headline,
            narrative.cause_of_death.narrative,
            narrative.epitaph.text,
            narrative.final_letter.text,
            narrative.whats_missing_narrative,
        ]
    ).lower()
    for banned in DEATH_VOCABULARY:
        assert banned not in words, f"{banned!r} appeared in: {words}"


def test_missing_sentence_has_no_doubled_article() -> None:
    """Regression: the sentence read "contains no a test suite"."""
    from necropsy.llm.validate import template_missing_narrative

    bare = _facts(2, has_tests=False, has_ci=False, has_license=False, has_contributing=False)
    sentence = template_missing_narrative(bare)
    assert "no a " not in sentence and "no an " not in sentence, sentence
    assert "no test suite" in sentence


def test_a_dead_repo_still_gets_the_full_narrative() -> None:
    """The boundary: ten days is where the autopsy begins."""
    facts = _facts(LIVING_THRESHOLD_DAYS)
    result = narrate(None, facts, score(ScorecardInput.from_facts(facts)))
    assert result.narrative.living is False
    assert result.narrative.final_letter.source == "template"
    assert len(result.narrative.resurrection_plan) == 3


def test_payload_reports_status() -> None:
    analysis = factories.make_analysis()
    assert analysis.to_payload(cache_status="hit")["status"] == "deceased"
    living = analysis.model_copy(update={"narrative": living_narrative(analysis.facts)})
    payload = living.to_payload(cache_status="hit")
    assert payload["status"] == "living" and payload["living"] is True


# -- the pulse line --------------------------------------------------------
def test_pulse_line_states_only_what_is_known() -> None:
    assert pulse_line(0)[0] == "Last commit today."
    assert pulse_line(1)[0] == "Last commit yesterday."
    assert pulse_line(4)[0] == "Last commit 4 days ago."
    assert "week" in pulse_line(4)[1]
    last_living_day = LIVING_THRESHOLD_DAYS - 1
    assert f"{LIVING_THRESHOLD_DAYS} days" in pulse_line(last_living_day)[1]
    for days in range(LIVING_THRESHOLD_DAYS):
        text = " ".join(pulse_line(days)).lower()
        assert not any(w in text for w in DEATH_VOCABULARY), text


# -- end to end through the real service -----------------------------------
def _living_service(db: Database):
    """A service whose scripted GitHub reports a commit two days before `now`."""
    base = specimens.by_slug()["gemstone-cart"]
    recent = dataclasses.replace(
        base,
        name="busy-project",
        # 2026-10-29 09:00 to the pipeline's frozen "now" of 2026-11-01 00:00 is
        # 2.6 days, which floors to the 2 days the assertions expect.
        last_commit_at="2026-10-29T09:00:00+00:00",
        last_commit_message="fix login redirect",
    )
    payloads = specimens.build_payloads(recent)["payloads"]
    github = ScriptedGitHub(db, payloads)
    guard = RaisingLlmClient()
    service = AnalysisService(
        db, github=github, llm=guard, now=lambda: datetime(2026, 11, 1, tzinfo=UTC)
    )
    return service, guard


def test_pipeline_gives_a_living_repo_a_checkup_and_no_model_call(db: Database) -> None:
    service, guard = _living_service(db)
    result = service.analyze("github.com/necropsy-specimens/busy-project")
    assert result.analysis.narrative.living is True
    assert guard.calls == 0, "no provider call may be made for a living repo"
    assert result.analysis.facts.days_since_last_commit == 2


def test_a_living_repo_is_not_hung_on_the_wall(db: Database) -> None:
    service, _ = _living_service(db)
    service.analyze("github.com/necropsy-specimens/busy-project")
    conn = db.connect()
    assert exhibits.list_exhibits(conn) == []
    assert exhibits.count_exhibits(conn) == 0
    # ...but its own page still resolves, so the paste flow can land on it.
    found = exhibits.get_by_slug(conn, "necropsy-specimens--busy-project")
    assert found is not None and found.is_living is True


def test_the_living_page_is_a_checkup_not_a_plaque(db: Database) -> None:
    service, _ = _living_service(db)
    service.analyze("github.com/necropsy-specimens/busy-project")
    app = create_app(db=db, config=Config())
    app.testing = True
    body = app.test_client().get("/exhibit/necropsy-specimens--busy-project").get_data(as_text=True)

    assert "Still breathing" in body
    assert "Last commit 2 days ago." in body
    assert "Vital signs" in body
    assert "What it could use" in body
    # None of the deathly sections exist on this page.
    for absent in ("The letter it never sent", "Resurrection plan", "Cause of death",
                   "Inscribe a line", "Share this tombstone", "Revival difficulty"):
        assert absent not in body, absent
    lowered = body.lower()
    assert "abandon" not in lowered and "exhume" not in lowered


# -- the surfaces that must not treat a living repo as dead -----------------
def test_write_revival_spec_declines_a_living_repo(db: Database) -> None:
    service, _ = _living_service(db)
    service.analyze("github.com/necropsy-specimens/busy-project")
    result = NecropsyTools(db).write_revival_spec({"slug": "necropsy-specimens--busy-project"})
    assert result["ok"] is True and result["living"] is True
    assert result["spec_markdown"] is None
    assert "nothing to revive" in result["message"]


def test_list_graveyard_excludes_a_living_repo(db: Database) -> None:
    seed(db)
    before = NecropsyTools(db).list_graveyard({})["total"]
    service, _ = _living_service(db)
    service.analyze("github.com/necropsy-specimens/busy-project")
    after = NecropsyTools(db).list_graveyard({})
    assert after["total"] == before
    assert all("busy-project" not in e["slug"] for e in after["exhibits"])


def test_gallery_does_not_list_a_living_repo(db: Database) -> None:
    seed(db)
    service, _ = _living_service(db)
    service.analyze("github.com/necropsy-specimens/busy-project")
    app = create_app(db=db, config=Config())
    app.testing = True
    client: FlaskClient = app.test_client()
    assert "busy-project" not in client.get("/").get_data(as_text=True)
