"""AnalysisService end to end, offline (R-1, R-3, E1, E8, Property 11).

The service is the single entry point, so these tests exercise the whole pipeline
through it rather than through the stage functions. The GitHub side is driven by a
scripted client that replays a specimen's committed payloads in call-plan order;
no `respx`, no sockets. The point is the ordering and the guarantees, not GitHub's
wire format, which `test_github_client.py` already covers.

The guarantee that gets the most attention is Property 11: a cache hit, and the
`allow_network=False` path, must reach neither GitHub nor the LLM. Both are
asserted with doubles that raise on use, so a regression is a failed test rather
than a silent cost.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from necropsy.analysis.service import AnalysisService, offline_service
from necropsy.errors import OfflineCacheMiss
from necropsy.github.client import BudgetScope, GitHubClient, GitHubResponse
from necropsy.graveyard import specimens
from necropsy.llm.client import FakeLlmClient, RaisingLlmClient
from necropsy.store.cache import CacheStatus
from necropsy.store.db import Database


class ScriptedGitHub(GitHubClient):
    """A GitHub client that answers from a specimen's payloads, counting calls.

    It honours the real budget counter (so the ≤10 ceiling is still enforced) but
    never opens a socket. Each endpoint maps to the matching key in the payload
    map, so the fetch stage walks its real call plan over canned data.
    """

    def __init__(self, db: Database, payloads: dict[str, object]) -> None:
        super().__init__(db, http=_ClosedHttp())
        self._payloads = payloads
        self.network_calls = 0

    def begin_analysis(self) -> BudgetScope:
        self.calls_used = 0
        self.network_calls = 0
        return BudgetScope(client=self, budget=self.budget)

    def get(self, path: str, *, per_page: int, params=None, tolerate_404=False, tolerate_403=False) -> GitHubResponse:  # type: ignore[override]
        self.network_calls += 1
        self._charge(path)
        key, body = self._route(path)
        if body is None:
            return GitHubResponse(endpoint_key=key, path=path, status=404)
        return GitHubResponse(endpoint_key=key, path=path, status=200, body=body)

    def _route(self, path: str) -> tuple[str, object]:
        p = self._payloads
        if path.endswith("/commits"):
            # The live /commits endpoint returns a list; `resolve_head` reads
            # `[0]`. The specimen stores the single head-commit object (the shape
            # `fetch` persists), so wrap it back into a one-element list here.
            head = p["head_commit"]
            return "head", head if isinstance(head, list) else [head]
        if path.endswith("/languages"):
            return "languages", p["languages"]
        if "/git/trees/" in path:
            return "tree", p["tree"]
        if path.endswith("/readme"):
            return "readme", p["readme"]
        if path.endswith("/releases/latest"):
            return "release", p["latest_release"]
        if "/contents/" in path:
            name = path.split("/contents/", 1)[1]
            contents = p["contents"]
            assert isinstance(contents, dict)
            return "contents", contents.get(name)
        if path.endswith("/issues"):
            return "issues", p["open_issues"]
        # The repo metadata call is the only bare `/repos/{o}/{r}` path.
        return "repo", p["repo"]


class _ClosedHttp:
    """Stands in for httpx.Client so the base class has something to close."""

    def close(self) -> None:
        return None


@pytest.fixture
def specimen_payloads() -> dict[str, object]:
    spec = specimens.by_slug()["gemstone-cart"]
    return specimens.build_payloads(spec)["payloads"]


def _fixed_now() -> datetime:
    return datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)


# -- the miss path ---------------------------------------------------------
def test_miss_runs_the_pipeline_and_returns_seven_sections(
    db: Database, specimen_payloads: dict[str, object]
) -> None:
    """R-1.2, R-3.1: a cold analysis produces the whole post-mortem."""
    github = ScriptedGitHub(db, specimen_payloads)
    service = AnalysisService(db, github=github, llm=None, build_llm=False, now=_fixed_now)

    result = service.analyze("github.com/necropsy-specimens/gemstone-cart")
    assert result.status is CacheStatus.MISS

    payload = result.payload()
    for section in (
        "what_was_built", "time_of_death", "cause_of_death",
        "whats_missing", "scorecard", "epitaph", "resurrection_plan",
    ):
        assert section in payload
    assert payload["scorecard"]["score"] > 0
    assert github.network_calls <= 9, "the declared plan uses at most 9 calls (E2)"
    assert payload["provenance"]["github_calls_used"] == github.network_calls


def test_a_second_identical_request_is_a_cache_hit(
    db: Database, specimen_payloads: dict[str, object]
) -> None:
    """R-2.1, R-2.2: the second call resolves the SHA, finds the row, stops.

    One GitHub call (the SHA resolution), zero LLM calls, and a byte-identical
    payload save for the two volatile fields.
    """
    github = ScriptedGitHub(db, specimen_payloads)
    service = AnalysisService(db, github=github, llm=None, build_llm=False, now=_fixed_now)

    first = service.analyze("github.com/necropsy-specimens/gemstone-cart")

    # begin_analysis() resets the per-analysis counter, so this reads the second
    # analysis's spend alone: one call to resolve the SHA, then a cache hit.
    second = service.analyze("github.com/necropsy-specimens/gemstone-cart")
    assert second.status is CacheStatus.HIT
    assert github.network_calls == 1, "a hit spends only the SHA resolution (R-2.2)"

    from necropsy.store.cache import persisted_view

    assert persisted_view(first.payload()) == persisted_view(second.payload())


# -- Property 11 -----------------------------------------------------------
def test_offline_path_touches_neither_github_nor_the_llm(
    db: Database, specimen_payloads: dict[str, object]
) -> None:
    """R-2.5, Property 11: allow_network=False makes zero calls of either kind."""
    # Seed one analysis the normal way.
    github = ScriptedGitHub(db, specimen_payloads)
    seeded = AnalysisService(db, github=github, llm=None, build_llm=False, now=_fixed_now)
    seeded.analyze("github.com/necropsy-specimens/gemstone-cart")

    # Now serve it with clients that raise if used.
    offline = offline_service(db)
    offline.llm = RaisingLlmClient()  # type: ignore[assignment]
    result = offline.analyze(
        "github.com/necropsy-specimens/gemstone-cart", allow_network=False
    )
    assert result.status is CacheStatus.HIT


def test_offline_miss_is_an_explicit_error_not_a_fetch(db: Database) -> None:
    """R-11.5: a cache-only request for an unknown repo fails, making no call."""
    offline = offline_service(db)
    with pytest.raises(OfflineCacheMiss):
        offline.analyze("github.com/nobody/never-analyzed", allow_network=False)


def test_the_llm_is_never_reached_on_a_hit(
    db: Database, specimen_payloads: dict[str, object]
) -> None:
    """E1: the narrator sits behind the cache miss; a hit cannot reach it.

    The miss is served with a scripted LLM so the first analysis narrates; the hit
    is served with a client that raises, proving the second request never narrates.
    """
    github = ScriptedGitHub(db, specimen_payloads)
    scripted_llm = FakeLlmClient.returning(_valid_narrative())
    service = AnalysisService(db, github=github, llm=scripted_llm, now=_fixed_now)

    service.analyze("github.com/necropsy-specimens/gemstone-cart")
    assert scripted_llm.calls == 1

    service.llm = RaisingLlmClient()  # type: ignore[assignment]
    hit = service.analyze("github.com/necropsy-specimens/gemstone-cart")
    assert hit.status is CacheStatus.HIT  # did not raise => LLM not called


def test_llm_failure_degrades_the_narrative_not_the_analysis(
    db: Database, specimen_payloads: dict[str, object]
) -> None:
    """R-3.4, E10: a dead provider still persists facts and scorecard."""
    github = ScriptedGitHub(db, specimen_payloads)
    broken = FakeLlmClient.returning("not json", "still not json")
    service = AnalysisService(db, github=github, llm=broken, now=_fixed_now)

    result = service.analyze("github.com/necropsy-specimens/gemstone-cart")
    payload = result.payload()
    assert payload["scorecard"]["score"] > 0
    assert payload["provenance"]["llm_status"] == "failed"
    assert payload["epitaph"]["source"] == "unavailable"


def _valid_narrative() -> dict[str, object]:
    """A minimal grounded narrative for the gemstone-cart specimen."""
    return {
        "what_was_built_summary": "A Rails shopping-cart engine.",
        "cause_of_death": {
            "headline": "Pinned to a Rails release past end of life",
            "narrative": "The Gemfile pins an unsupported Rails.",
            "evidence": [
                {
                    "fact_key": "primary_language",
                    "fact_value": "Ruby",
                    "claim": "Ruby is the primary language by bytes.",
                }
            ],
        },
        "whats_missing_narrative": "Only a legacy CI service is configured.",
        "final_letter": (
            "You left me a Gemfile pinned to rails and 312 files. "
            "The last thing you wrote was a version bump. "
            "I have waited here in Ruby for years. I am still here."
        ),
        "epitaph": "A Ruby cart, pinned and quiet.",
        "epitaph_evidence": {"fact_key": "primary_language", "fact_value": "Ruby"},
        "resurrection_plan": [
            {
                "order": 1,
                "title": "Replace the deprecated dependencies",
                "detail": "Update the Gemfile one gem at a time.",
                "addresses_factor": "dependency_rot",
                "estimated_effort": "days",
            },
            {
                "order": 2,
                "title": "Add a modern CI workflow",
                "detail": "Create .github/workflows/ci.yml; it does not exist yet.",
                "addresses_factor": "ci_absence",
                "estimated_effort": "hours",
            },
            {
                "order": 3,
                "title": "Record what still works",
                "detail": "Create STATUS.md; it does not exist yet.",
                "addresses_factor": "abandonment",
                "estimated_effort": "hours",
            },
        ],
        "confidence_note": "Sampled from the open issues.",
    }
