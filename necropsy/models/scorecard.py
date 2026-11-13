"""Scorecard models.

ScorecardInput is the *complete* input to scoring. The scorer reads nothing
else: no clock, no RNG, no env, no database (E6, Property 2). Every named
deficit and asset input from R-20.3/R-20.4 is a field here so the monotonicity
properties can mutate them individually.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # import-light at runtime; facts.py does not import this module
    from necropsy.models.facts import NormalizedFacts

Confidence = Literal["high", "medium", "low"]


class ScorecardInput(BaseModel):
    model_config = {"frozen": True}

    # -- deficit inputs (R-20.3): increasing these must never lower the score
    test_suite_missing: bool = True
    ci_missing: bool = True
    license_missing: bool = True
    contributing_missing: bool = True
    docs_missing: bool = True
    days_since_last_commit: int = 0
    deprecated_dependency_count: int = 0
    unanswered_issue_ratio: float = 0.0
    archived: bool = False

    # -- asset inputs (R-20.4): increasing these must never raise the score
    test_file_count: int = 0
    has_ci: bool = False
    has_license: bool = False
    has_contributing: bool = False
    doc_page_count: int = 0
    fresh_dependency_count: int = 0
    maintainer_response_count: int = 0

    # -- supporting inputs (not part of the monotonicity contract) ----------
    ci_is_legacy_only: bool = False
    license_is_osi: bool = False
    readme_chars: int = 0
    has_changelog: bool = False
    declared_wip: bool = False
    direct_dependency_count: int = 0
    eol_runtime_marker_count: int = 0
    has_lockfile: bool = False
    manifest_found: bool = False
    dependency_data_confidence: Confidence = "low"
    issue_data_available: bool = False
    days_since_issue_activity: int | None = None
    open_issues_count: int = 0
    open_issue_sample_size: int = 0
    loc_estimate: int = 0
    file_count: int = 0
    language_count: int = 0

    @classmethod
    def from_facts(cls, facts: NormalizedFacts) -> ScorecardInput:
        """Project `NormalizedFacts` onto the scoring surface.

        The projection is written out field by field rather than filtered from a
        `model_dump`, so adding a fact cannot silently become a scoring input. A
        new input has to be named here, which is where the deficit/asset contract
        in R-20.3 and R-20.4 is declared.
        """
        f = facts
        return cls(
            test_suite_missing=not f.has_tests,
            ci_missing=not f.has_ci,
            license_missing=not f.has_license,
            contributing_missing=not f.has_contributing,
            docs_missing=f.doc_page_count == 0,
            days_since_last_commit=f.days_since_last_commit,
            deprecated_dependency_count=f.deprecated_dependency_count,
            unanswered_issue_ratio=f.unanswered_issue_ratio,
            archived=f.is_archived,
            test_file_count=f.test_file_count,
            has_ci=f.has_ci,
            has_license=f.has_license,
            has_contributing=f.has_contributing,
            doc_page_count=f.doc_page_count,
            fresh_dependency_count=f.fresh_dependency_count,
            maintainer_response_count=f.maintainer_response_count,
            ci_is_legacy_only=f.ci_is_legacy_only,
            license_is_osi=f.license_is_osi,
            readme_chars=f.readme_chars,
            has_changelog=f.has_changelog,
            declared_wip=f.declared_wip,
            direct_dependency_count=f.direct_dependency_count,
            eol_runtime_marker_count=len(f.eol_runtime_markers),
            has_lockfile=f.has_lockfile,
            manifest_found=f.manifest_found,
            dependency_data_confidence=f.dependency_data_confidence,
            issue_data_available=f.issue_data_available,
            days_since_issue_activity=f.days_since_issue_activity,
            open_issues_count=f.open_issues_count,
            open_issue_sample_size=f.open_issue_sample_size,
            loc_estimate=f.loc_estimate,
            file_count=f.file_count,
            language_count=len(f.languages),
        )


class Factor(BaseModel):
    key: str
    label: str
    weight: int
    normalized: float
    contribution: float
    confidence: Confidence
    raw_inputs: dict[str, object]
    rationale: str


class Band(BaseModel):
    key: str
    label: str
    range: tuple[int, int]


class Scorecard(BaseModel):
    scorecard_version: int
    score: int
    total_unrounded: float
    band: Band
    factors: list[Factor]
    data_gaps: list[str] = Field(default_factory=list)
    weights_checksum: str

    def factor(self, key: str) -> Factor | None:
        return next((f for f in self.factors if f.key == key), None)

    def contributing_factors(self) -> list[Factor]:
        """Factors with a non-zero contribution, heaviest first (R-10.2)."""
        return sorted(
            (f for f in self.factors if f.contribution > 0),
            key=lambda f: f.contribution,
            reverse=True,
        )
