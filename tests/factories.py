"""Deterministic builders for store and payload tests.

Every value is fixed. `as_of` is a literal, not `now()`, so a stored analysis
built here is byte-stable across runs and machines (E6, E13).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from necropsy import ANALYSIS_SCHEMA_VERSION, PROMPT_TEMPLATE_VERSION
from necropsy.models.analysis import Analysis, Provenance, RepoRef
from necropsy.models.facts import Dependency, NormalizedFacts
from necropsy.models.narrative import (
    CauseOfDeath,
    Epitaph,
    Evidence,
    LlmNarrative,
    ResurrectionStep,
)
from necropsy.models.scorecard import Scorecard, ScorecardInput
from necropsy.scoring import score
from necropsy.store.analyses import cache_key

AS_OF = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
LAST_COMMIT_AT = datetime(2019, 6, 14, 9, 30, 0, tzinfo=UTC)
CREATED_AT = datetime(2015, 2, 2, 8, 0, 0, tzinfo=UTC)
ROW_CREATED_AT = datetime(2026, 3, 1, 12, 0, 5, tzinfo=UTC)
SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"


def make_facts(
    owner: str = "octocat",
    name: str = "abandoned-cart",
    *,
    sha: str = SHA,
    **overrides: Any,
) -> NormalizedFacts:
    facts = NormalizedFacts(
        canonical_url=f"github.com/{owner}/{name}".lower(),
        owner=owner,
        name=name,
        html_url=f"https://github.com/{owner}/{name}",
        description="A shopping cart nobody checked out.",
        topics=["ecommerce", "rails"],
        as_of=AS_OF,
        languages={"Ruby": 412_000, "JavaScript": 61_000},
        primary_language="Ruby",
        loc_estimate=14_200,
        loc_confidence="medium",
        file_count=312,
        top_level_entries=["app", "config", "Gemfile", "README.md"],
        readme_excerpt="A shopping cart for Rails 4.2 apps.",
        readme_chars=1_840,
        inferred_purpose_source="readme",
        size_kb=8_412,
        stars=94,
        forks_count=12,
        last_commit_sha=sha,
        last_commit_at=LAST_COMMIT_AT,
        last_commit_message="Bump version",
        days_since_last_commit=(AS_OF - LAST_COMMIT_AT).days,
        created_at=CREATED_AT,
        repo_age_days=(AS_OF - CREATED_AT).days,
        has_tests=True,
        test_file_count=18,
        test_paths_sample=["spec/cart_spec.rb"],
        has_ci=True,
        ci_systems=["travis"],
        ci_is_legacy_only=True,
        has_license=True,
        license_spdx="MIT",
        license_is_osi=True,
        doc_page_count=2,
        has_lockfile=True,
        ecosystems=["ruby"],
        manifest_paths=["Gemfile"],
        manifest_found=True,
        direct_dependency_count=14,
        dependencies=[Dependency(name="rails", spec="~> 4.2", ecosystem="ruby")],
        deprecated_dependencies=[
            Dependency(
                name="rails",
                spec="~> 4.2",
                ecosystem="ruby",
                deprecated=True,
                deprecated_since="2017-04-27",
                replacement="rails >= 7.0",
                source_url="https://endoflife.date/rails",
            )
        ],
        deprecated_dependency_count=1,
        fresh_dependency_count=13,
        dependency_data_confidence="high",
        deprecation_table_version="deps-v1 (2026-01-15)",
        issue_data_available=True,
        open_issues_count=23,
        open_issue_sample_size=23,
        unanswered_issue_count=17,
        unanswered_issue_ratio=17 / 23,
        maintainer_response_count=2,
        days_since_issue_activity=1_400,
        data_gaps=[],
    )
    return facts.model_copy(update=overrides) if overrides else facts


def make_scorecard(facts: NormalizedFacts) -> Scorecard:
    return score(ScorecardInput.from_facts(facts))


def make_narrative(facts: NormalizedFacts) -> LlmNarrative:
    return LlmNarrative(
        what_was_built_summary="A Rails shopping-cart engine, last touched in 2019.",
        cause_of_death=CauseOfDeath(
            headline="Pinned to a Rails release that reached end of life first",
            narrative="The Gemfile pins Rails 4.2, which went out of support before the "
            "final commit landed.",
            evidence=[
                Evidence(
                    fact_key="deprecated_dependency_count",
                    fact_value="1",
                    claim="One direct dependency is flagged by the offline deprecation table.",
                )
            ],
            source="llm",
        ),
        whats_missing_narrative="Tests exist, but only a legacy CI service runs them.",
        whats_missing_narrative_source="llm",
        epitaph=Epitaph(
            text="Pinned to Rails 4.2, which reached end of life before its last commit.",
            source="llm",
            evidence=Evidence(
                fact_key="primary_language",
                fact_value="Ruby",
                claim="Ruby is the primary language by bytes.",
            ),
        ),
        resurrection_plan=[
            ResurrectionStep(
                order=1,
                title="Replace .travis.yml with a GitHub Actions workflow",
                detail="Create .github/workflows/ci.yml; the file does not exist yet.",
                addresses_factor="ci_absence",
                estimated_effort="hours",
            )
        ],
        resurrection_plan_source="llm",
        summary_source="llm",
        confidence_note=f"Issue signals sampled from {facts.open_issue_sample_size} open issues.",
        llm_status="ok",
    )


def make_analysis(
    owner: str = "octocat",
    name: str = "abandoned-cart",
    *,
    sha: str = SHA,
    llm_status: str = "ok",
    schema_version: int = ANALYSIS_SCHEMA_VERSION,
    facts: NormalizedFacts | None = None,
) -> Analysis:
    resolved = facts or make_facts(owner, name, sha=sha)
    ref = RepoRef(owner=resolved.owner, name=resolved.name)
    scorecard = make_scorecard(resolved)
    narrative = make_narrative(resolved)
    return Analysis(
        repo=ref,
        slug=ref.slug,
        cache_key=cache_key(resolved.canonical_url, resolved.last_commit_sha, schema_version),
        facts=resolved,
        scorecard=scorecard,
        narrative=narrative,
        provenance=Provenance(
            github_calls_used=9,
            llm_status=llm_status,
            llm_model="fake-model-1",
            prompt_template_version=PROMPT_TEMPLATE_VERSION,
            scorecard_version=scorecard.scorecard_version,
            analysis_schema_version=schema_version,
            deprecation_table_version=resolved.deprecation_table_version,
            duration_ms=4_210,
        ),
        created_at=ROW_CREATED_AT,
    )


def make_raw_payloads(owner: str = "octocat", name: str = "abandoned-cart") -> dict[str, Any]:
    """A stand-in for the stored raw payload map. Shape, not fidelity, is what
    the store tests care about; real fixtures arrive with T-1.7."""
    return {
        "repo": {"full_name": f"{owner}/{name}", "size": 8_412, "archived": False},
        "languages": {"Ruby": 412_000, "JavaScript": 61_000},
        "head_commit": {"sha": SHA},
    }
