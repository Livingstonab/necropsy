"""The scorer (design.md section 5).

`score()` is a pure function of ScorecardInput. It reads no clock, no RNG, no
environment, no database, no network (E6). `as_of` arrives already reduced to
day counts in the input, which is why a stored score cannot drift (Property 2).

Monotonicity is structural, not incidental. Every factor below is written as a
monotone non-decreasing function of its deficit inputs and monotone
non-increasing in its asset inputs; weights are non-negative; the total is
their weighted sum. `clamp` preserves monotonicity because clamping is itself
monotone. Properties 3 and 4 verify this claim empirically over generated input.
"""

from __future__ import annotations

import math

from necropsy import SCORECARD_VERSION
from necropsy.models.scorecard import Confidence, Factor, Scorecard, ScorecardInput
from necropsy.scoring.bands import band_for
from necropsy.scoring.weights import (
    FACTOR_ORDER,
    LABELS,
    NEUTRAL_DEPENDENCY_ROT,
    NEUTRAL_MAINTAINER_ABSENCE,
    WEIGHTS,
    weights_checksum,
)


# --------------------------------------------------------------------------
# ramp helpers (shared, monotone, pure)
# --------------------------------------------------------------------------
def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def linear_ramp(x: float, lo: float, hi: float) -> float:
    """0 at or below `lo`, 1 at or above `hi`, linear between. Monotone in x."""
    if hi <= lo:
        raise ValueError("linear_ramp requires hi > lo")
    return clamp((x - lo) / (hi - lo))


def log_ramp(x: float, lo: float, hi: float) -> float:
    """Log-scaled ramp for quantities spanning orders of magnitude. Monotone in x."""
    if hi <= lo or lo <= 0:
        raise ValueError("log_ramp requires hi > lo > 0")
    xs = max(float(x), lo)
    return clamp((math.log10(xs) - math.log10(lo)) / (math.log10(hi) - math.log10(lo)))


class _FactorResult:
    __slots__ = ("normalized", "confidence", "raw_inputs", "rationale", "gap")

    def __init__(
        self,
        normalized: float,
        confidence: Confidence,
        raw_inputs: dict[str, object],
        rationale: str,
        gap: str | None = None,
    ) -> None:
        self.normalized = clamp(normalized)
        self.confidence = confidence
        self.raw_inputs = raw_inputs
        self.rationale = rationale
        self.gap = gap


# --------------------------------------------------------------------------
# factors
# --------------------------------------------------------------------------
def _abandonment(i: ScorecardInput) -> _FactorResult:
    value = linear_ramp(i.days_since_last_commit, 90, 1825)
    rationale = (
        f"Last commit {i.days_since_last_commit} days ago; ramp 90d (0.0) to 1825d (1.0)."
    )
    if i.archived:
        value = max(value, 0.85)
        rationale += " Owner archived the repository, so the value is floored at 0.85."
    return _FactorResult(
        value,
        "high",
        {"days_since_last_commit": i.days_since_last_commit, "archived": i.archived},
        rationale,
    )


def _dependency_rot(i: ScorecardInput) -> _FactorResult:
    if not i.manifest_found:
        return _FactorResult(
            NEUTRAL_DEPENDENCY_ROT,
            "low",
            {"manifest_found": False},
            f"No dependency manifest was found, so this factor uses its documented "
            f"neutral default of {NEUTRAL_DEPENDENCY_ROT}. Treat it as unknown, not healthy.",
            gap="dependencies",
        )

    deprecated = max(0, i.deprecated_dependency_count)
    fresh = max(0, i.fresh_dependency_count)
    denominator = max(i.direct_dependency_count, deprecated + fresh, 1)
    value = clamp(deprecated / denominator)
    parts = [
        f"{deprecated} of {denominator} direct dependencies flagged by the offline "
        f"deprecation table ({value:.2f})"
    ]

    eol = min(max(0, i.eol_runtime_marker_count), 2)
    if eol:
        value += 0.25 * eol
        parts.append(f"+{0.25 * eol:.2f} for {eol} end-of-life runtime marker(s)")

    if not i.has_lockfile:
        value += 0.10
        parts.append("+0.10 for no lockfile")

    confidence: Confidence = (
        "high"
        if i.dependency_data_confidence == "high" and i.direct_dependency_count > 0
        else "medium"
        if i.direct_dependency_count > 0
        else "low"
    )
    return _FactorResult(
        value,
        confidence,
        {
            "deprecated_dependency_count": deprecated,
            "fresh_dependency_count": fresh,
            "direct_dependency_count": i.direct_dependency_count,
            "eol_runtime_marker_count": i.eol_runtime_marker_count,
            "has_lockfile": i.has_lockfile,
        },
        "; ".join(parts) + ".",
    )


def _test_absence(i: ScorecardInput) -> _FactorResult:
    if i.test_suite_missing:
        base = 1.0
        detail = "No test files detected in the file tree (1.00)."
    else:
        count = max(0, i.test_file_count)
        if count <= 4:
            base = 0.6
        elif count <= 19:
            base = 0.3
        else:
            base = 0.1
        detail = f"{count} test file(s) detected ({base:.2f})."

    value = base
    if i.has_ci:
        value -= 0.10
        detail += " -0.10 because a CI configuration implies the tests are run."

    return _FactorResult(
        value,
        "high",
        {
            "test_suite_missing": i.test_suite_missing,
            "test_file_count": i.test_file_count,
            "has_ci": i.has_ci,
        },
        detail,
    )


def _maintainer_absence(i: ScorecardInput) -> _FactorResult:
    if not i.issue_data_available:
        return _FactorResult(
            NEUTRAL_MAINTAINER_ABSENCE,
            "low",
            {"issue_data_available": False, "open_issues_count": i.open_issues_count},
            f"Issue data was unavailable, so this factor uses its documented neutral "
            f"default of {NEUTRAL_MAINTAINER_ABSENCE}.",
            gap="issues",
        )

    ratio = clamp(i.unanswered_issue_ratio)
    parts = [f"{ratio:.0%} of sampled open issues have no replies (weighted 0.6)"]

    if i.days_since_issue_activity is None:
        staleness = 0.5
        parts.append("issue-activity date unavailable, using 0.5 for the staleness term")
        confidence: Confidence = "low"
        gap: str | None = "issue_activity_date"
    else:
        staleness = linear_ramp(i.days_since_issue_activity, 30, 730)
        parts.append(
            f"newest issue activity {i.days_since_issue_activity} days ago "
            f"({staleness:.2f}, weighted 0.4)"
        )
        # A capped sample is an honest "medium", not a confident "high" (P7).
        sampled = 0 < i.open_issue_sample_size < i.open_issues_count
        confidence = "medium" if sampled else "high"
        gap = None

    value = 0.6 * ratio + 0.4 * staleness

    responses = min(max(0, i.maintainer_response_count), 5)
    if responses:
        value -= 0.10 * (responses / 5)
        parts.append(f"-{0.10 * responses / 5:.2f} for {responses} answered issue(s)")

    rationale = "; ".join(parts) + "."
    if i.archived:
        value = max(value, 0.9)
        rationale += " Repository is archived, so the value is floored at 0.90."

    return _FactorResult(
        value,
        confidence,
        {
            "unanswered_issue_ratio": round(ratio, 4),
            "days_since_issue_activity": i.days_since_issue_activity,
            "maintainer_response_count": i.maintainer_response_count,
            "open_issues_count": i.open_issues_count,
            "archived": i.archived,
        },
        rationale,
        gap=gap,
    )


def _documentation_deficit(i: ScorecardInput) -> _FactorResult:
    value = 1.0
    parts: list[str] = ["Starts at 1.00"]

    if i.readme_chars >= 400:
        value -= 0.40
        parts.append(f"-0.40 README has {i.readme_chars} characters")
    if i.readme_chars >= 2000:
        value -= 0.15
        parts.append("-0.15 README is substantial (>=2000 chars)")
    if i.doc_page_count >= 3:
        value -= 0.20
        parts.append(f"-0.20 {i.doc_page_count} documentation pages")
    if i.doc_page_count >= 10:
        value -= 0.10
        parts.append("-0.10 extensive documentation (>=10 pages)")
    if i.has_changelog:
        value -= 0.08
        parts.append("-0.08 CHANGELOG present")
    if i.has_contributing:
        value -= 0.12
        parts.append("-0.12 CONTRIBUTING present")
    if i.declared_wip:
        value += 0.10
        parts.append("+0.10 README declares the project unfinished")
    if i.docs_missing:
        value += 0.10
        parts.append("+0.10 no documentation beyond the README")
    if i.contributing_missing:
        value += 0.05
        parts.append("+0.05 no CONTRIBUTING file")

    return _FactorResult(
        value,
        "high" if i.readme_chars > 0 else "medium",
        {
            "readme_chars": i.readme_chars,
            "doc_page_count": i.doc_page_count,
            "has_changelog": i.has_changelog,
            "has_contributing": i.has_contributing,
            "declared_wip": i.declared_wip,
        },
        "; ".join(parts) + ".",
    )


def _codebase_scale(i: ScorecardInput) -> _FactorResult:
    loc_term = log_ramp(i.loc_estimate, 500, 200_000)
    file_term = log_ramp(i.file_count, 20, 5_000)
    lang_term = clamp(i.language_count / 6)
    value = 0.6 * loc_term + 0.3 * file_term + 0.1 * lang_term
    return _FactorResult(
        value,
        "medium",
        {
            "loc_estimate": i.loc_estimate,
            "file_count": i.file_count,
            "language_count": i.language_count,
        },
        f"Estimated {i.loc_estimate} LOC ({loc_term:.2f}, weighted 0.6), "
        f"{i.file_count} files ({file_term:.2f}, weighted 0.3), "
        f"{i.language_count} languages ({lang_term:.2f}, weighted 0.1). "
        f"Bigger codebases take longer to revive.",
    )


def _ci_absence(i: ScorecardInput) -> _FactorResult:
    if i.ci_missing or not i.has_ci:
        value, detail = 1.0, "No CI configuration found in the file tree."
    elif i.ci_is_legacy_only:
        value, detail = 0.6, "Only a legacy CI service is configured (Travis or AppVeyor)."
    else:
        value, detail = 0.0, "A modern CI configuration is present."
    return _FactorResult(
        value,
        "high",
        {"has_ci": i.has_ci, "ci_is_legacy_only": i.ci_is_legacy_only},
        detail,
    )


def _legal_risk(i: ScorecardInput) -> _FactorResult:
    if i.license_missing or not i.has_license:
        value, detail = 1.0, "No LICENSE file: reuse is legally unclear by default."
    elif i.license_is_osi:
        value, detail = 0.0, "An OSI-approved licence is present."
    else:
        value, detail = 0.5, "A licence is present but is not OSI-approved or not recognized."
    return _FactorResult(
        value,
        "high",
        {"has_license": i.has_license, "license_is_osi": i.license_is_osi},
        detail,
    )


_FACTOR_FNS = {
    "abandonment": _abandonment,
    "dependency_rot": _dependency_rot,
    "test_absence": _test_absence,
    "maintainer_absence": _maintainer_absence,
    "documentation_deficit": _documentation_deficit,
    "codebase_scale": _codebase_scale,
    "ci_absence": _ci_absence,
    "legal_risk": _legal_risk,
}


def score(inp: ScorecardInput) -> Scorecard:
    """Compute the revival difficulty scorecard. Pure: same input, same output."""
    factors: list[Factor] = []
    gaps: list[str] = []
    total = 0.0

    for key in FACTOR_ORDER:
        result = _FACTOR_FNS[key](inp)
        weight = WEIGHTS[key]
        contribution = result.normalized * weight
        total += contribution
        if result.gap and result.gap not in gaps:
            gaps.append(result.gap)
        factors.append(
            Factor(
                key=key,
                label=LABELS[key],
                weight=weight,
                normalized=round(result.normalized, 6),
                contribution=round(contribution, 6),
                confidence=result.confidence,
                raw_inputs=result.raw_inputs,
                rationale=result.rationale,
            )
        )

    # Report the total from the rounded contributions so that the displayed
    # breakdown adds up exactly to the displayed total (Property 7).
    total_unrounded = round(sum(f.contribution for f in factors), 6)
    integer_score = int(round(total_unrounded))
    integer_score = max(0, min(100, integer_score))

    return Scorecard(
        scorecard_version=SCORECARD_VERSION,
        score=integer_score,
        total_unrounded=total_unrounded,
        band=band_for(integer_score),
        factors=factors,
        data_gaps=gaps,
        weights_checksum=weights_checksum(),
    )
