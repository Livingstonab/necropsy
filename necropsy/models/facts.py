"""NormalizedFacts: the only repository representation the scorer and the LLM see.

Every age field derives from `as_of`, which is frozen at fetch time. Nothing
downstream is permitted to consult a clock (E6, Property 2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from necropsy import LIVING_THRESHOLD_DAYS

Confidence = Literal["high", "medium", "low"]


class Dependency(BaseModel):
    name: str
    spec: str | None = None
    ecosystem: str
    deprecated: bool = False
    deprecated_since: str | None = None
    replacement: str | None = None
    source_url: str | None = None


class NormalizedFacts(BaseModel):
    """Pure, idempotent projection of raw GitHub payloads (Property 8)."""

    # -- identity -----------------------------------------------------------
    canonical_url: str
    owner: str
    name: str
    html_url: str
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    is_fork: bool = False
    is_archived: bool = False
    as_of: datetime

    # -- what was built -----------------------------------------------------
    languages: dict[str, int] = Field(default_factory=dict)
    primary_language: str | None = None
    loc_estimate: int = 0
    loc_confidence: Confidence = "low"
    file_count: int = 0
    top_level_entries: list[str] = Field(default_factory=list)
    readme_excerpt: str | None = None
    readme_chars: int = 0
    declared_wip: bool = False
    wip_marker: str | None = None
    inferred_purpose_source: Literal["readme", "description", "manifest", "none"] = "none"
    size_kb: int = 0
    stars: int = 0
    forks_count: int = 0

    # -- time of death ------------------------------------------------------
    last_commit_sha: str
    last_commit_at: datetime
    last_commit_message: str | None = None
    days_since_last_commit: int = 0
    created_at: datetime | None = None
    repo_age_days: int | None = None
    last_release_tag: str | None = None
    last_release_at: datetime | None = None
    days_since_last_release: int | None = None

    # -- structural presence ------------------------------------------------
    has_tests: bool = False
    test_file_count: int = 0
    test_paths_sample: list[str] = Field(default_factory=list)
    has_ci: bool = False
    ci_systems: list[str] = Field(default_factory=list)
    ci_is_legacy_only: bool = False
    has_license: bool = False
    license_spdx: str | None = None
    license_is_osi: bool = False
    has_contributing: bool = False
    has_code_of_conduct: bool = False
    doc_page_count: int = 0
    has_changelog: bool = False
    has_dockerfile: bool = False
    has_lockfile: bool = False

    # -- dependencies -------------------------------------------------------
    ecosystems: list[str] = Field(default_factory=list)
    manifest_paths: list[str] = Field(default_factory=list)
    manifest_found: bool = False
    direct_dependency_count: int = 0
    dependencies: list[Dependency] = Field(default_factory=list)
    deprecated_dependencies: list[Dependency] = Field(default_factory=list)
    deprecated_dependency_count: int = 0
    fresh_dependency_count: int = 0
    eol_runtime_markers: list[str] = Field(default_factory=list)
    dependency_data_confidence: Confidence = "low"
    deprecation_table_version: str | None = None

    # -- maintainer signals -------------------------------------------------
    issue_data_available: bool = False
    open_issues_count: int = 0
    open_issue_sample_size: int = 0
    unanswered_issue_count: int = 0
    unanswered_issue_ratio: float = 0.0
    maintainer_response_count: int = 0
    newest_issue_activity_at: datetime | None = None
    days_since_issue_activity: int | None = None
    oldest_open_issue_at: datetime | None = None

    # -- provenance ---------------------------------------------------------
    data_gaps: list[str] = Field(default_factory=list)

    # -- life or death ------------------------------------------------------
    @property
    def is_living(self) -> bool:
        """Active, not dead: committed to within `LIVING_THRESHOLD_DAYS` and not
        archived. Derived from facts alone, so it is deterministic (E6)."""
        return (not self.is_archived) and self.days_since_last_commit < LIVING_THRESHOLD_DAYS

    # -- grounding ----------------------------------------------------------
    def groundable(self) -> dict[str, object]:
        """Flat fact map used to verify every narrative claim (Property 12).

        Only scalar-ish values are exposed: a claim must cite something a
        reader could check on the repository page.
        """
        out: dict[str, object] = {}
        for key, value in self.model_dump(mode="json").items():
            if key in {"readme_excerpt", "dependencies", "deprecated_dependencies"}:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                out[key] = value
            elif isinstance(value, list) and all(isinstance(v, str) for v in value):
                out[key] = ", ".join(value)
        out["deprecated_dependency_names"] = ", ".join(
            d.name for d in self.deprecated_dependencies
        )
        return out
