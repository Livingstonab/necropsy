"""The assembled post-mortem, and the one payload shape every surface returns.

`Analysis.to_payload()` is the single source of the response contract in
design.md section 7.1. The web app and the MCP server both return it verbatim,
which is what keeps them honest adapters (E8).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from necropsy.models.facts import NormalizedFacts
from necropsy.models.narrative import LlmNarrative, MissingItem
from necropsy.models.scorecard import Scorecard

#: The one host NECROPSY reads (R-4.3). A `RepoRef` can only ever name this
#: host, because `canonicalize()` refuses every other one, so `host` is a
#: derived constant rather than a field — there is no way to construct an
#: identity that disagrees with it.
CANONICAL_HOST = "github.com"


class RepoRef(BaseModel):
    """A canonical repository identity. Produced only by github.urls.canonicalize."""

    model_config = {"frozen": True}

    owner: str
    name: str

    @property
    def host(self) -> str:
        return CANONICAL_HOST

    @property
    def canonical_url(self) -> str:
        """The lowercased identity that feeds the cache key (R-4.1, E3)."""
        return f"{CANONICAL_HOST}/{self.owner}/{self.name}".lower()

    @property
    def display_url(self) -> str:
        """The pasted case, preserved for the plaque (R-4.1)."""
        return f"{CANONICAL_HOST}/{self.owner}/{self.name}"

    @property
    def html_url(self) -> str:
        return f"https://{CANONICAL_HOST}/{self.owner}/{self.name}"

    @property
    def slug(self) -> str:
        return f"{self.owner}--{self.name}".lower()

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.name}"


class Provenance(BaseModel):
    github_calls_used: int = 0
    llm_status: str = "skipped"
    llm_model: str | None = None
    prompt_template_version: int | None = None
    scorecard_version: int = 1
    analysis_schema_version: int = 1
    deprecation_table_version: str | None = None
    duration_ms: int = 0
    data_gaps: list[str] = []
    fixture_source: str | None = None


MISSING_ITEM_SPEC: list[tuple[str, str]] = [
    ("tests", "Test suite"),
    ("ci", "Continuous integration"),
    ("license", "LICENSE"),
    ("docs", "Documentation"),
    ("contributing", "CONTRIBUTING"),
    ("changelog", "CHANGELOG"),
    ("lockfile", "Dependency lockfile"),
    ("fresh_dependencies", "Dependency freshness"),
]


class Analysis(BaseModel):
    """One repository, at one commit, fully examined."""

    repo: RepoRef
    slug: str
    cache_key: str
    facts: NormalizedFacts
    scorecard: Scorecard
    narrative: LlmNarrative
    provenance: Provenance
    created_at: datetime

    # -- derived ------------------------------------------------------------
    def merged_data_gaps(self) -> list[str]:
        """Facts gaps then scorecard gaps, de-duplicated, order preserved.

        Derived rather than carried, so a payload rebuilt from a cached row
        reports exactly the gaps the stored facts and scorecard imply (R-2.4).
        """
        gaps: list[str] = []
        for gap in [*self.facts.data_gaps, *self.scorecard.data_gaps]:
            if gap not in gaps:
                gaps.append(gap)
        return gaps

    def missing_items(self) -> list[MissingItem]:
        """Deterministic checklist (R-13 'whats_missing'). Not LLM-generated."""
        f = self.facts
        rows: list[tuple[str, bool, str]] = [
            (
                "tests",
                f.has_tests,
                f"{f.test_file_count} test file(s) detected in the tree"
                if f.has_tests
                else "no test paths found in the file tree",
            ),
            (
                "ci",
                f.has_ci,
                f"{', '.join(f.ci_systems)}"
                + (" (legacy service only)" if f.ci_is_legacy_only else "")
                if f.has_ci
                else "no CI configuration in the tree",
            ),
            (
                "license",
                f.has_license,
                f"{f.license_spdx or 'present but unrecognized'}"
                + ("" if f.license_is_osi else " (not OSI-approved)")
                if f.has_license
                else "no LICENSE or COPYING file",
            ),
            (
                "docs",
                f.doc_page_count > 0,
                f"{f.doc_page_count} documentation page(s) beyond the README"
                if f.doc_page_count
                else "no docs/ directory or extra documentation pages",
            ),
            (
                "contributing",
                f.has_contributing,
                "CONTRIBUTING present" if f.has_contributing else "no CONTRIBUTING file",
            ),
            (
                "changelog",
                f.has_changelog,
                "CHANGELOG present" if f.has_changelog else "no CHANGELOG file",
            ),
            (
                "lockfile",
                f.has_lockfile,
                "lockfile present" if f.has_lockfile else "no dependency lockfile",
            ),
            (
                "fresh_dependencies",
                f.manifest_found and f.deprecated_dependency_count == 0,
                (
                    f"{f.deprecated_dependency_count} of {f.direct_dependency_count} "
                    f"direct dependencies flagged by the offline deprecation table"
                    if f.manifest_found
                    else "no dependency manifest found; freshness unknown"
                ),
            ),
        ]
        labels = dict(MISSING_ITEM_SPEC)
        return [
            MissingItem(key=k, label=labels[k], present=present, detail=detail)
            for k, present, detail in rows
        ]

    def to_payload(
        self,
        *,
        cache_status: Literal["hit", "miss"],
        served_at: datetime | None = None,
    ) -> dict[str, Any]:
        """The response contract. `cache_status` and `served_at` are assembled
        here and never persisted, which is what makes Property 9 hold (R-2.4)."""
        f = self.facts
        return {
            "ok": True,
            "cache_status": cache_status,
            "status": "living" if self.narrative.living else "deceased",
            "living": self.narrative.living,
            "served_at": (served_at or datetime.now().astimezone()).isoformat(),
            "slug": self.slug,
            "cache_key": self.cache_key,
            "analysis_schema_version": self.provenance.analysis_schema_version,
            "as_of": f.as_of.isoformat(),
            "repo": {
                "canonical_url": f.canonical_url,
                "owner": f.owner,
                "name": f.name,
                "html_url": f.html_url,
                "description": f.description,
                "topics": f.topics,
                "is_fork": f.is_fork,
                "is_archived": f.is_archived,
                "stars": f.stars,
                "forks_count": f.forks_count,
            },
            "what_was_built": {
                "summary": self.narrative.what_was_built_summary,
                "summary_source": self.narrative.summary_source,
                "primary_language": f.primary_language,
                "languages": f.languages,
                "loc_estimate": f.loc_estimate,
                "loc_confidence": f.loc_confidence,
                "file_count": f.file_count,
                "size_kb": f.size_kb,
                "top_level_entries": f.top_level_entries,
                "inferred_purpose_source": f.inferred_purpose_source,
                "ecosystems": f.ecosystems,
            },
            "time_of_death": {
                "last_commit_sha": f.last_commit_sha,
                "last_commit_at": f.last_commit_at.isoformat(),
                "last_commit_message": f.last_commit_message,
                "days_since_last_commit": f.days_since_last_commit,
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "repo_age_days": f.repo_age_days,
                "last_release_tag": f.last_release_tag,
                "last_release_at": (
                    f.last_release_at.isoformat() if f.last_release_at else None
                ),
                "days_since_last_release": f.days_since_last_release,
            },
            "cause_of_death": self.narrative.cause_of_death.model_dump(),
            "final_letter": self.narrative.final_letter.model_dump(),
            "whats_missing": {
                "items": [i.model_dump() for i in self.missing_items()],
                "narrative": self.narrative.whats_missing_narrative,
                "narrative_source": self.narrative.whats_missing_narrative_source,
            },
            "scorecard": self.scorecard.model_dump(),
            "epitaph": self.narrative.epitaph.model_dump(),
            "resurrection_plan": [s.model_dump() for s in self.narrative.resurrection_plan],
            "resurrection_plan_source": self.narrative.resurrection_plan_source,
            "maintainer_signals": {
                "issue_data_available": f.issue_data_available,
                "open_issues_count": f.open_issues_count,
                "open_issue_sample_size": f.open_issue_sample_size,
                "unanswered_issue_count": f.unanswered_issue_count,
                "unanswered_issue_ratio": round(f.unanswered_issue_ratio, 4),
                "maintainer_response_count": f.maintainer_response_count,
                "days_since_issue_activity": f.days_since_issue_activity,
            },
            "provenance": {
                **self.provenance.model_dump(),
                "data_gaps": self.merged_data_gaps(),
                "confidence_note": self.narrative.confidence_note,
                "rejected_stages": self.narrative.rejected_stages,
            },
        }
