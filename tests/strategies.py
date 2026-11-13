"""Shared Hypothesis strategies. Defined once here and reused (E11, R-20.9).

Design §2.2 names this module as the single home for generated test data. It
exists so that two property tests can never disagree about what a valid
repository identity looks like. T-1.5 populates the repository-identity half;
T-2.2 adds `scorecard_inputs()` and its deficit/asset mutators alongside it.

Everything here is pure data construction: no network, no filesystem, no clock
(E11). Strategies are exposed as functions rather than module-level constants
so a caller can compose or map them without mutating shared state.
"""

from __future__ import annotations

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from necropsy.models.analysis import RepoRef
from necropsy.models.scorecard import ScorecardInput

#: A GitHub account name: ASCII alphanumerics with single inner hyphens. This
#: is a deliberately narrow subset of what `urls._OWNER_RE` accepts — wide
#: enough to exercise the hyphen rule and the case rules, bounded so generation
#: stays cheap.
OWNER_PATTERN = r"\A[a-zA-Z0-9](?:[a-zA-Z0-9]{0,20}-)?[a-zA-Z0-9]\Z"

#: A repository name: alphanumerics plus dot, hyphen and underscore.
NAME_PATTERN = r"\A[a-zA-Z0-9][a-zA-Z0-9._-]{0,30}[a-zA-Z0-9]\Z"

#: Hosts that are not github.com, for the R-4.3 rejection property. Each has a
#: dot so the schemeless parser reads it as a host rather than as an owner.
NON_GITHUB_HOSTS: tuple[str, ...] = (
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
    "git.sr.ht",
    "gitea.example.com",
    "gist.github.com",
    "api.github.com",
    "pages.github.com",
    "github.com.evil.test",
    "notgithub.com",
    "raw.githubusercontent.com",
)


def repo_owners() -> SearchStrategy[str]:
    """Owner names that `canonicalize()` must accept."""
    return st.from_regex(OWNER_PATTERN, fullmatch=True)


def repo_names() -> SearchStrategy[str]:
    """Repository names that `canonicalize()` must accept.

    Names ending in `.git` are excluded: `repo.git` and `repo` are two spellings
    of *one* identity, so a generated `repo.git` would make the equivalence
    property compare the wrong pair rather than expose a defect (R-4.1).
    """
    return st.from_regex(NAME_PATTERN, fullmatch=True).filter(
        lambda name: not name.lower().endswith(".git")
    )


def owner_repo_pairs() -> SearchStrategy[tuple[str, str]]:
    """An `(owner, name)` pair, for tests that need both at once."""
    return st.tuples(repo_owners(), repo_names())


def repo_refs() -> SearchStrategy[RepoRef]:
    """A canonical identity, bypassing the parser.

    For downstream tests (cache keys, slugs, call plans) that need an identity
    but are not testing how it was spelled.
    """
    return st.builds(RepoRef, owner=repo_owners(), name=repo_names())


def non_github_hosts() -> SearchStrategy[str]:
    """Hosts NECROPSY must refuse with `UNSUPPORTED_HOST` (R-4.3)."""
    return st.sampled_from(NON_GITHUB_HOSTS)


def equivalent_url_forms(owner: str, name: str) -> list[str]:
    """Every spelling R-4.1 and R-4.2 declare to mean this one repository.

    Not a strategy: a deterministic expansion of a generated pair, so a failing
    example names the exact form that drifted.
    """
    return [
        f"{owner}/{name}",
        f"{owner}/{name}/",
        f"/{owner}/{name}",
        f"github.com/{owner}/{name}",
        f"www.github.com/{owner}/{name}",
        f"http://github.com/{owner}/{name}",
        f"https://github.com/{owner}/{name}",
        f"https://www.github.com/{owner}/{name}",
        f"https://github.com/{owner}/{name}/",
        f"https://github.com/{owner}/{name}.git",
        f"https://github.com/{owner}/{name}.git/",
        f"https://github.com/{owner}/{name}/tree/main",
        f"https://github.com/{owner}/{name}/blob/main/README.md",
        f"https://github.com/{owner}/{name}/issues/1",
        f"https://github.com/{owner}/{name}?utm_source=x",
        f"https://github.com/{owner}/{name}#readme",
        f"https://github.com:443/{owner}/{name}",
        f"  https://github.com/{owner}/{name}  ",
    ]


def unsupported_url_forms(host: str, owner: str, name: str) -> list[str]:
    """The same spellings, pointed at a host NECROPSY does not read."""
    return [
        f"{host}/{owner}/{name}",
        f"http://{host}/{owner}/{name}",
        f"https://{host}/{owner}/{name}",
        f"https://{host}/{owner}/{name}.git",
        f"https://{host}/{owner}/{name}/tree/main",
    ]


# --------------------------------------------------------------------------
# scorecard inputs (T-2.2, R-20.9)
# --------------------------------------------------------------------------
#: Deficit inputs: increasing any one of these must never lower the score
#: (R-20.3). The value is a strategy for a *larger* value, used by the
#: monotonicity tests to step one field at a time.
DEFICIT_FIELDS: tuple[str, ...] = (
    "test_suite_missing",
    "ci_missing",
    "license_missing",
    "contributing_missing",
    "docs_missing",
    "days_since_last_commit",
    "deprecated_dependency_count",
    "unanswered_issue_ratio",
    "archived",
)

#: Asset inputs: increasing any one of these must never raise the score (R-20.4).
ASSET_FIELDS: tuple[str, ...] = (
    "test_file_count",
    "has_ci",
    "has_license",
    "has_contributing",
    "doc_page_count",
    "fresh_dependency_count",
    "maintainer_response_count",
)


def scorecard_inputs() -> SearchStrategy[ScorecardInput]:
    """A valid, internally consistent `ScorecardInput`.

    Consistency matters more than range here. Generating `has_license=True` with
    `license_missing=True` would describe a repository that cannot exist, and a
    monotonicity counterexample found in that region would say nothing about the
    scorer. So the paired deficit/asset flags are derived from one draw each, and
    the mutators below maintain the pairing when they step a field.
    """
    return st.builds(
        _build_input,
        has_tests=st.booleans(),
        test_file_count=st.integers(min_value=0, max_value=400),
        has_ci=st.booleans(),
        ci_is_legacy_only=st.booleans(),
        has_license=st.booleans(),
        license_is_osi=st.booleans(),
        has_contributing=st.booleans(),
        doc_page_count=st.integers(min_value=0, max_value=60),
        readme_chars=st.integers(min_value=0, max_value=30_000),
        has_changelog=st.booleans(),
        declared_wip=st.booleans(),
        days_since_last_commit=st.integers(min_value=0, max_value=8_000),
        archived=st.booleans(),
        manifest_found=st.booleans(),
        direct_dependency_count=st.integers(min_value=0, max_value=200),
        deprecated_dependency_count=st.integers(min_value=0, max_value=200),
        eol_runtime_marker_count=st.integers(min_value=0, max_value=4),
        has_lockfile=st.booleans(),
        dependency_data_confidence=st.sampled_from(("high", "medium", "low")),
        issue_data_available=st.booleans(),
        open_issues_count=st.integers(min_value=0, max_value=5_000),
        open_issue_sample_size=st.integers(min_value=0, max_value=30),
        unanswered_issue_ratio=st.floats(
            min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
        ),
        maintainer_response_count=st.integers(min_value=0, max_value=30),
        days_since_issue_activity=st.one_of(
            st.none(), st.integers(min_value=0, max_value=4_000)
        ),
        loc_estimate=st.integers(min_value=0, max_value=5_000_000),
        file_count=st.integers(min_value=0, max_value=5_000),
        language_count=st.integers(min_value=0, max_value=12),
    )


def _build_input(
    *,
    has_tests: bool,
    test_file_count: int,
    has_ci: bool,
    has_license: bool,
    has_contributing: bool,
    doc_page_count: int,
    manifest_found: bool,
    direct_dependency_count: int,
    deprecated_dependency_count: int,
    **rest: object,
) -> ScorecardInput:
    """Derive the paired fields so no impossible repository is ever generated."""
    deprecated = min(deprecated_dependency_count, direct_dependency_count)
    return ScorecardInput(
        test_suite_missing=not has_tests,
        test_file_count=test_file_count if has_tests else 0,
        ci_missing=not has_ci,
        has_ci=has_ci,
        license_missing=not has_license,
        has_license=has_license,
        contributing_missing=not has_contributing,
        has_contributing=has_contributing,
        docs_missing=doc_page_count == 0,
        doc_page_count=doc_page_count,
        manifest_found=manifest_found,
        direct_dependency_count=direct_dependency_count if manifest_found else 0,
        deprecated_dependency_count=deprecated if manifest_found else 0,
        fresh_dependency_count=(
            max(0, direct_dependency_count - deprecated) if manifest_found else 0
        ),
        **rest,  # type: ignore[arg-type]
    )


def increase_deficit(inp: ScorecardInput, field: str) -> ScorecardInput | None:
    """A copy of `inp` with one deficit input raised, everything else fixed.

    Returns `None` when the field is already at its maximum, which the property
    treats as "nothing to compare" rather than as a pass.
    """
    if field in {"test_suite_missing", "ci_missing", "license_missing",
                 "contributing_missing", "docs_missing", "archived"}:
        if getattr(inp, field):
            return None
        update: dict[str, object] = {field: True}
        # Keep the paired asset flag consistent: "the licence is missing" and
        # "the licence is present" cannot both be true.
        paired = {
            "test_suite_missing": {"test_file_count": 0},
            "ci_missing": {"has_ci": False},
            "license_missing": {"has_license": False, "license_is_osi": False},
            "contributing_missing": {"has_contributing": False},
            "docs_missing": {"doc_page_count": 0},
        }.get(field, {})
        update.update(paired)
        return inp.model_copy(update=update)

    if field == "unanswered_issue_ratio":
        if inp.unanswered_issue_ratio >= 1.0:
            return None
        return inp.model_copy(
            update={"unanswered_issue_ratio": min(1.0, inp.unanswered_issue_ratio + 0.25)}
        )

    if field == "deprecated_dependency_count":
        # More deprecated dependencies out of the same total: one fresh
        # dependency becomes deprecated, so the denominator does not move.
        if not inp.manifest_found or inp.fresh_dependency_count == 0:
            return None
        return inp.model_copy(
            update={
                "deprecated_dependency_count": inp.deprecated_dependency_count + 1,
                "fresh_dependency_count": inp.fresh_dependency_count - 1,
            }
        )

    if field == "days_since_last_commit":
        return inp.model_copy(
            update={"days_since_last_commit": inp.days_since_last_commit + 400}
        )

    raise AssertionError(f"{field} is not a deficit input")


def increase_asset(inp: ScorecardInput, field: str) -> ScorecardInput | None:
    """A copy of `inp` with one asset input raised, everything else fixed."""
    if field in {"has_ci", "has_license", "has_contributing"}:
        if getattr(inp, field):
            return None
        deficit = {
            "has_ci": "ci_missing",
            "has_license": "license_missing",
            "has_contributing": "contributing_missing",
        }[field]
        return inp.model_copy(update={field: True, deficit: False})

    if field == "test_file_count":
        return inp.model_copy(
            update={"test_file_count": inp.test_file_count + 5, "test_suite_missing": False}
        )

    if field == "doc_page_count":
        return inp.model_copy(
            update={"doc_page_count": inp.doc_page_count + 3, "docs_missing": False}
        )

    if field == "fresh_dependency_count":
        if not inp.manifest_found:
            return None
        # One more dependency, and it is a fresh one: the deprecated count is
        # unchanged, so this is purely an asset increase.
        return inp.model_copy(
            update={
                "fresh_dependency_count": inp.fresh_dependency_count + 1,
                "direct_dependency_count": inp.direct_dependency_count + 1,
            }
        )

    if field == "maintainer_response_count":
        if not inp.issue_data_available or inp.maintainer_response_count >= 5:
            return None
        return inp.model_copy(
            update={"maintainer_response_count": inp.maintainer_response_count + 1}
        )

    raise AssertionError(f"{field} is not an asset input")
