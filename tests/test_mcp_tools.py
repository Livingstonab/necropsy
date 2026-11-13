"""The MCP tool surface (R-11, E8, Property 11).

The tools are exercised through `NecropsyTools` directly rather than over stdio:
the stdio wiring is three one-line handlers, and what needs testing is the
contract — schema conformance, the shared error envelope, and the zero-network
guarantee on the two read tools. A transport in the loop would test the SDK, not
NECROPSY.

The zero-network claim is asserted, not assumed: `get_exhibit` and
`list_graveyard` run through `offline_service`, whose GitHub client raises on
use, so a regression that reached for a fetch fails here (Property 11).
"""

from __future__ import annotations

import jsonschema
import pytest

from necropsy.graveyard import seed
from necropsy.mcp import schemas
from necropsy.mcp.server import NecropsyTools
from necropsy.store.analyses import save_analysis
from necropsy.store.db import Database
from tests import factories


@pytest.fixture
def seeded(db: Database) -> Database:
    seed(db)
    return db


@pytest.fixture
def tools(seeded: Database) -> NecropsyTools:
    return NecropsyTools(seeded)


# -- schemas are well-formed ----------------------------------------------
@pytest.mark.parametrize(
    "schema",
    [
        schemas.ANALYZE_REPO_INPUT,
        schemas.GET_EXHIBIT_INPUT,
        schemas.LIST_GRAVEYARD_INPUT,
        schemas.WRITE_REVIVAL_SPEC_INPUT,
    ],
)
def test_input_schemas_are_valid_json_schema(schema: dict) -> None:
    jsonschema.Draft202012Validator.check_schema(schema)


def test_the_tool_surface_is_closed() -> None:
    """R-11.1 + the revival bridge: four tools, no more."""
    assert set(schemas.TOOL_DESCRIPTIONS) == {
        "analyze_repo",
        "get_exhibit",
        "list_graveyard",
        "write_revival_spec",
    }


# -- list_graveyard --------------------------------------------------------
def test_list_graveyard_returns_summaries(tools: NecropsyTools) -> None:
    result = tools.list_graveyard({})
    assert result["ok"] is True
    assert result["total"] >= 6
    assert len(result["exhibits"]) == result["total"]
    first = result["exhibits"][0]
    for key in ("slug", "repo_url", "score", "band", "epitaph", "days_since_last_commit"):
        assert key in first
    assert isinstance(first["is_demo"], bool)


def test_list_graveyard_honours_sort(tools: NecropsyTools) -> None:
    hardest = tools.list_graveyard({"sort": "hardest"})["exhibits"]
    scores = [e["score"] for e in hardest]
    assert scores == sorted(scores, reverse=True), "hardest sorts by score descending"


def test_list_graveyard_honours_limit_and_offset(tools: NecropsyTools) -> None:
    page = tools.list_graveyard({"limit": 3, "offset": 0})
    assert len(page["exhibits"]) == 3
    assert page["total"] >= 6, "total is the whole set, not the page"


def test_list_graveyard_makes_no_network_call(tools: NecropsyTools) -> None:
    """R-11.6. The offline service's client raises if touched; this must not."""
    result = tools.list_graveyard({})
    assert result["ok"] is True  # completed => the raising client was never used


# -- get_exhibit -----------------------------------------------------------
#: The canonical exhibit slug is owner--name (design §3), not the manifest's
#: shorter curator key.
GEMSTONE = "necropsy-specimens--gemstone-cart"


def test_get_exhibit_by_slug(tools: NecropsyTools) -> None:
    payload = tools.get_exhibit({"slug": GEMSTONE})
    assert payload["ok"] is True
    assert payload["slug"] == GEMSTONE
    assert payload["cache_status"] == "hit"
    assert "scorecard" in payload and "resurrection_plan" in payload


def test_get_exhibit_by_repo_url(tools: NecropsyTools) -> None:
    payload = tools.get_exhibit(
        {"repo_url": "https://github.com/necropsy-specimens/gemstone-cart"}
    )
    assert payload["slug"] == GEMSTONE


def test_get_exhibit_exposes_the_full_scorecard(tools: NecropsyTools) -> None:
    """R-11 / E5: a tool never returns an opaque score."""
    card = tools.get_exhibit({"slug": GEMSTONE})["scorecard"]
    assert card["score"] == round(card["total_unrounded"])
    assert len(card["factors"]) == 8
    for factor in card["factors"]:
        for field in ("key", "raw_inputs", "normalized", "weight", "contribution",
                      "confidence", "rationale"):
            assert field in factor, field
    assert "weights_checksum" in card


def test_get_exhibit_miss_is_not_in_graveyard(tools: NecropsyTools) -> None:
    """R-11.5. A cache miss is an error code, not a triggered analysis.

    Through `call`, which is the dispatch the transport uses and the place the
    error taxonomy is closed: a raised `NotInGraveyard` becomes an envelope here.
    """
    envelope = tools.call("get_exhibit", {"repo_url": "github.com/nobody/never-seen"})
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "NOT_IN_GRAVEYARD"


def test_get_exhibit_never_calls_the_network(tools: NecropsyTools) -> None:
    """R-11.5, Property 11. Even a miss makes no call: offline_service raises."""
    # A hit and a miss both complete without the raising client firing.
    assert tools.get_exhibit({"slug": "necropsy-specimens--solo-sync"})["ok"] is True
    miss = tools.call("get_exhibit", {"slug": "not-a-real-slug"})
    assert miss["ok"] is False and miss["error"]["code"] == "NOT_IN_GRAVEYARD"


# -- analyze_repo, offline -------------------------------------------------
def test_analyze_repo_offline_serves_a_cached_exhibit(tools: NecropsyTools) -> None:
    """R-11.4 shape: allow_network=false returns the cached analysis."""
    payload = tools.call(
        "analyze_repo",
        {"url": "github.com/necropsy-specimens/atlas-mapper", "allow_network": False},
    )
    assert payload["ok"] is True
    assert payload["cache_status"] == "hit"
    assert payload["slug"] == "necropsy-specimens--atlas-mapper"


def test_analyze_repo_offline_miss_is_an_envelope(tools: NecropsyTools) -> None:
    """A cache-only analyze of an unknown repo is a code, not an exception."""
    envelope = tools.call(
        "analyze_repo", {"url": "github.com/nobody/never", "allow_network": False}
    )
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "OFFLINE_CACHE_MISS"


# -- the error boundary ----------------------------------------------------
def test_an_unknown_tool_is_an_envelope_not_a_raise(tools: NecropsyTools) -> None:
    result = tools.call("delete_everything", {})
    assert result["ok"] is False
    assert "available" in result["error"]["details"]


def test_a_bad_url_is_a_mapped_code(tools: NecropsyTools) -> None:
    """R-11.8. A validation failure comes back as the shared envelope."""
    result = tools.call("analyze_repo", {"url": "not a url at all", "allow_network": False})
    assert result["ok"] is False
    assert result["error"]["code"] in {"INVALID_URL", "UNSUPPORTED_HOST"}
    assert "correlation_id" in result["error"]


def test_call_dispatch_matches_direct_invocation(tools: NecropsyTools) -> None:
    via_call = tools.call("list_graveyard", {"sort": "curated"})
    direct = tools.list_graveyard({"sort": "curated"})
    assert via_call == direct


# -- starts with no keys ---------------------------------------------------
def test_tools_work_with_an_empty_environment(seeded: Database) -> None:
    """R-11.10. No token, no LLM key: the read tools still serve."""
    tools = NecropsyTools(seeded)  # clean_env fixture guarantees an empty env
    assert tools.list_graveyard({})["ok"] is True
    assert tools.get_exhibit({"slug": "necropsy-specimens--closing-time"})["ok"] is True


# -- the server wires up ---------------------------------------------------
def test_build_server_registers_without_a_transport(seeded: Database) -> None:
    """The stdio wiring imports and constructs; running it needs a client."""
    from necropsy.mcp.server import build_server

    server = build_server(NecropsyTools(seeded))
    assert server is not None
    assert getattr(server, "name", None) == "necropsy"


def test_shared_pipeline_not_duplicated(db: Database) -> None:
    """R-11.7. The tool and a direct store read return the same payload.

    If the MCP layer grew its own scoring or assembly, the two would drift. They
    must not: there is one scorer and one assembler, used by both.
    """
    analysis = factories.make_analysis(owner="necropsy-specimens", name="probe")
    with db.transaction() as conn:
        save_analysis(conn, analysis, factories.make_raw_payloads("necropsy-specimens", "probe"))
    from necropsy.store import exhibits

    with db.transaction() as conn:
        row = save_analysis(
            conn, analysis, factories.make_raw_payloads("necropsy-specimens", "probe")
        )
        exhibits.upsert_exhibit(
            conn,
            repo_id=row.repo_id,
            analysis_id=row.id,
            slug=analysis.slug,
            plaque_title="necropsy-specimens/probe",
        )

    tools = NecropsyTools(db)
    via_tool = tools.get_exhibit({"slug": analysis.slug})
    direct = exhibits.get_by_slug(db.connect(), analysis.slug).analysis.to_payload(
        cache_status="hit"
    )
    # Both assemble cache_status and served_at at response time; compare the rest.
    via_tool.pop("served_at", None)
    direct.pop("served_at", None)
    assert via_tool == direct
