"""The revival-spec builder and the write_revival_spec MCP tool (R-12).

The spec is the Kiro bridge: a post-mortem turned into a document a spec session
can act on. The brief's acceptance check is explicit — the output must contain
all three plan steps and at least one evidence token — so that is asserted
directly, plus the shared-builder guarantee that the web button and the MCP tool
emit the same bytes (E8).
"""

from __future__ import annotations

import pytest

from necropsy.graveyard import seed
from necropsy.mcp.revival import build_revival_spec
from necropsy.mcp.server import NecropsyTools
from necropsy.store.db import Database
from tests import factories

EXHIBIT = "left-pad--left-pad"


def test_spec_contains_all_three_plan_steps() -> None:
    analysis = factories.make_analysis()
    # Give it a full three-step plan so the acceptance check is meaningful.
    from necropsy.llm.validate import fallback_plan

    plan = fallback_plan(analysis.facts, analysis.scorecard)
    analysis = analysis.model_copy(
        update={"narrative": analysis.narrative.model_copy(update={"resurrection_plan": plan})}
    )
    spec = build_revival_spec(analysis)

    assert spec.count("### Task") == 3
    for step in plan:
        assert step.title in spec
        assert step.addresses_factor in spec


def test_spec_contains_at_least_one_evidence_token() -> None:
    """The acceptance line from the brief: evidence must survive into the spec."""
    analysis = factories.make_analysis()
    spec = build_revival_spec(analysis)
    cited = analysis.narrative.cause_of_death.evidence
    assert cited, "fixture analysis should carry evidence"
    assert any(ev.fact_key in spec and ev.fact_value in spec for ev in cited)


def test_spec_carries_the_scorecard_table_and_epitaph() -> None:
    analysis = factories.make_analysis()
    spec = build_revival_spec(analysis)
    assert analysis.narrative.epitaph.text in spec
    assert "| Factor |" in spec
    assert "weights checksum" in spec.lower()
    # Every factor is a row.
    for factor in analysis.scorecard.factors:
        assert factor.label in spec


def test_spec_is_deterministic() -> None:
    analysis = factories.make_analysis()
    assert build_revival_spec(analysis) == build_revival_spec(analysis)


# -- the MCP tool ----------------------------------------------------------
@pytest.fixture
def tools(db: Database) -> NecropsyTools:
    seed(db)
    return NecropsyTools(db)


def test_write_revival_spec_tool_returns_markdown(tools: NecropsyTools) -> None:
    result = tools.write_revival_spec({"slug": EXHIBIT})
    assert result["ok"] is True
    assert result["slug"] == EXHIBIT
    spec = result["spec_markdown"]
    assert spec.count("### Task") == 3
    assert "Revival spec —" in spec


def test_write_revival_spec_miss_is_not_in_graveyard(tools: NecropsyTools) -> None:
    """R-11.5: a spec request for an unknown repo is an envelope, not an analysis."""
    result = tools.call("write_revival_spec", {"repo_url": "github.com/nobody/never"})
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_IN_GRAVEYARD"


def test_web_button_and_mcp_tool_agree(tools: NecropsyTools, db: Database) -> None:
    """E8: one builder, two surfaces. The strings must be identical."""
    from necropsy.store import exhibits
    from necropsy.web.app import _full_spec

    exhibit = exhibits.get_by_slug(db.connect(), EXHIBIT)
    assert exhibit is not None
    via_web = _full_spec(exhibit.analysis)
    via_mcp = tools.write_revival_spec({"slug": EXHIBIT})["spec_markdown"]
    assert via_web == via_mcp
