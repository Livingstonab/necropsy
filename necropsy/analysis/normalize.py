"""Raw payloads to `NormalizedFacts` (design section 4.3). Pure and idempotent.

`NormalizedFacts` is the only repository representation the scorer and the LLM
ever see. Three rules hold here and nowhere else:

  - **`as_of` is an input, never a reading.** Every age field is derived from the
    `as_of` passed in, so a stored analysis cannot drift because a day passed
    (E6, Property 2). This module never calls `datetime.now()`.
  - **Idempotence (Property 8).** The same payloads and the same `as_of` produce an
    equal `NormalizedFacts`, every time, in any process. Ordering is made explicit
    wherever a set would otherwise leak iteration order into the output.
  - **A gap is recorded, never filled.** When a payload is missing, the affected
    fields keep their documented defaults and the fact key is appended to
    `data_gaps`, which the UI shows next to the number it affected (P7, R-8.4).

Repository content arrives as untrusted data and leaves as untrusted data: it is
decoded, truncated and stored, never executed, never used to build a path (E4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Literal

from necropsy.analysis import detect
from necropsy.analysis.fetch import RawPayloads, parse_github_time
from necropsy.analysis.loc import estimate_loc
from necropsy.github.endpoints import decode_base64_content
from necropsy.models.analysis import RepoRef
from necropsy.models.facts import Confidence, Dependency, NormalizedFacts

log = logging.getLogger("necropsy.analysis.normalize")

DEFAULT_README_CHARS = 4_000
DEFAULT_MANIFEST_CHARS = 2_000

#: OSI-approved SPDX identifiers GitHub commonly reports. A licence outside this
#: set is "present but not recognized as OSI-approved", which `legal_risk` scores
#: at 0.5 rather than 0.0 or 1.0 (design section 5.2).
OSI_APPROVED_SPDX = frozenset(
    {
        "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "GPL-2.0", "GPL-3.0",
        "LGPL-2.1", "LGPL-3.0", "AGPL-3.0", "MPL-2.0", "ISC", "Zlib", "EPL-1.0",
        "EPL-2.0", "Artistic-2.0", "BSL-1.0", "CDDL-1.0", "OSL-3.0", "PostgreSQL",
        "Unlicense", "NCSA", "EUPL-1.2", "MS-PL", "AFL-3.0", "LPPL-1.3c",
        "GPL-2.0-only", "GPL-3.0-only", "GPL-2.0-or-later", "GPL-3.0-or-later",
    }
)

#: SPDX values GitHub uses to mean "we could not tell".
_UNRECOGNIZED_SPDX = frozenset({"NOASSERTION", "", "NONE"})


def normalize(
    raw: RawPayloads,
    as_of: datetime,
    *,
    ref: RepoRef | None = None,
    readme_chars: int = DEFAULT_README_CHARS,
    manifest_chars: int = DEFAULT_MANIFEST_CHARS,
) -> NormalizedFacts:
    """Project raw payloads onto the fact surface.

    `readme_chars` and `manifest_chars` are passed in rather than read from the
    environment, so this function stays pure and its output stays reproducible
    from the stored payloads alone (E6, E3).
    """
    as_of = _as_utc(as_of)
    repo = raw.repo
    gaps: list[str] = []

    owner, name = _identity(repo, ref)
    reference = RepoRef(owner=owner, name=name)

    # -- time of death -----------------------------------------------------
    head = raw.head_commit if isinstance(raw.head_commit, dict) else {}
    commit = head.get("commit", {}) if isinstance(head, dict) else {}
    last_commit_sha = str(head.get("sha") or repo.get("default_branch_sha") or "")
    last_commit_at = (
        _commit_time(commit)
        or parse_github_time(repo.get("pushed_at"))
        or as_of
    )
    if not last_commit_sha:
        gaps.append("last_commit_sha")
    created_at = parse_github_time(repo.get("created_at"))

    release = raw.latest_release or {}
    release_at = parse_github_time(release.get("published_at") or release.get("created_at"))
    release_tag = str(release.get("tag_name")) if release.get("tag_name") else None
    if raw.latest_release is None:
        # Expected for most repositories; not a gap, just an absence.
        log.debug("no release payload for %s", reference.display_url)

    # -- structure ---------------------------------------------------------
    paths = raw.tree_paths
    if not paths:
        gaps.append("tree")
    signals = detect.detect_signals(paths, repo)

    # -- README ------------------------------------------------------------
    readme_text = decode_base64_content(raw.readme, limit=readme_chars)
    if readme_text is None:
        gaps.append("readme")
    declared_wip, wip_marker = detect.detect_declared_wip(readme_text)

    # -- languages and size ------------------------------------------------
    languages = dict(sorted(raw.languages.items(), key=lambda kv: (-kv[1], kv[0])))
    if not languages:
        gaps.append("languages")
    primary_language = (
        str(repo.get("language"))
        if repo.get("language")
        else (next(iter(languages), None))
    )
    skew_suspected = bool(paths) and signals.file_count > 0 and (
        len([p for p in paths if detect.is_vendored(p)]) > signals.file_count * 0.3
    )
    loc_estimate, loc_confidence = estimate_loc(
        languages, file_count=signals.file_count, skew_suspected=skew_suspected
    )

    # -- dependencies ------------------------------------------------------
    table = detect.load_deprecation_table()
    manifest_texts = _manifest_texts(raw, manifest_chars)
    dependencies, deprecated, dep_confidence = _dependencies(manifest_texts, table)
    eol_runtimes = detect.detect_eol_runtimes(manifest_texts, table)
    manifest_found = bool(manifest_texts)
    if not manifest_found:
        gaps.append("dependencies")

    # -- licence -----------------------------------------------------------
    spdx, license_is_osi = _license(repo)
    has_license = bool(spdx) or signals.has_license_file

    # -- maintainer signals ------------------------------------------------
    issues = _issue_signals(raw.open_issues, as_of)
    if not issues.available:
        gaps.append("issues")

    # -- assembly ----------------------------------------------------------
    description = repo.get("description")
    facts = NormalizedFacts(
        canonical_url=reference.canonical_url,
        owner=owner,
        name=name,
        html_url=str(repo.get("html_url") or reference.html_url),
        description=str(description) if description else None,
        topics=sorted(str(t) for t in (repo.get("topics") or []) if isinstance(t, str)),
        is_fork=bool(repo.get("fork")),
        is_archived=bool(repo.get("archived")),
        as_of=as_of,
        languages=languages,
        primary_language=primary_language,
        loc_estimate=loc_estimate,
        loc_confidence=loc_confidence,
        file_count=signals.file_count,
        top_level_entries=signals.top_level_entries,
        readme_excerpt=readme_text,
        readme_chars=len(readme_text) if readme_text else 0,
        declared_wip=declared_wip,
        wip_marker=wip_marker,
        inferred_purpose_source=_purpose_source(readme_text, description, manifest_found),
        size_kb=int(repo.get("size") or 0),
        stars=int(repo.get("stargazers_count") or 0),
        forks_count=int(repo.get("forks_count") or 0),
        last_commit_sha=last_commit_sha,
        last_commit_at=last_commit_at,
        last_commit_message=_commit_message(commit),
        days_since_last_commit=_days_between(last_commit_at, as_of),
        created_at=created_at,
        repo_age_days=_days_between(created_at, as_of) if created_at else None,
        last_release_tag=release_tag,
        last_release_at=release_at,
        days_since_last_release=_days_between(release_at, as_of) if release_at else None,
        has_tests=signals.has_tests,
        test_file_count=signals.test_file_count,
        test_paths_sample=signals.test_paths_sample,
        has_ci=signals.has_ci,
        ci_systems=signals.ci_systems,
        ci_is_legacy_only=signals.ci_is_legacy_only,
        has_license=has_license,
        license_spdx=spdx,
        license_is_osi=license_is_osi,
        has_contributing=signals.has_contributing,
        has_code_of_conduct=signals.has_code_of_conduct,
        doc_page_count=signals.doc_page_count,
        has_changelog=signals.has_changelog,
        has_dockerfile=signals.has_dockerfile,
        has_lockfile=signals.has_lockfile,
        ecosystems=signals.ecosystems,
        manifest_paths=sorted(manifest_texts),
        manifest_found=manifest_found,
        direct_dependency_count=len(dependencies),
        dependencies=dependencies,
        deprecated_dependencies=deprecated,
        deprecated_dependency_count=len(deprecated),
        fresh_dependency_count=max(0, len(dependencies) - len(deprecated)),
        eol_runtime_markers=[r.marker for r in eol_runtimes],
        dependency_data_confidence=dep_confidence,
        deprecation_table_version=table.label,
        issue_data_available=issues.available,
        open_issues_count=int(repo.get("open_issues_count") or 0),
        open_issue_sample_size=issues.sample_size,
        unanswered_issue_count=issues.unanswered,
        unanswered_issue_ratio=issues.unanswered_ratio,
        maintainer_response_count=issues.answered,
        newest_issue_activity_at=issues.newest_activity_at,
        days_since_issue_activity=issues.days_since_activity,
        oldest_open_issue_at=issues.oldest_at,
        data_gaps=sorted(set(gaps)),
    )
    return facts


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _as_utc(moment: datetime) -> datetime:
    """Treat a naive `as_of` as UTC rather than guessing a local zone."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _identity(repo: dict[str, Any], ref: RepoRef | None) -> tuple[str, str]:
    owner_block = repo.get("owner")
    owner = (
        str(owner_block.get("login"))
        if isinstance(owner_block, dict) and owner_block.get("login")
        else (ref.owner if ref else "")
    )
    name = str(repo.get("name")) if repo.get("name") else (ref.name if ref else "")
    if not owner or not name:
        full = str(repo.get("full_name") or "")
        if "/" in full:
            owner, name = full.split("/", 1)
    if not owner or not name:
        raise ValueError("cannot normalize a payload with no repository identity")
    return owner, name


def _commit_time(commit: dict[str, Any]) -> datetime | None:
    for block in ("committer", "author"):
        entry = commit.get(block)
        if isinstance(entry, dict):
            parsed = parse_github_time(entry.get("date"))
            if parsed is not None:
                return parsed
    return None


def _commit_message(commit: dict[str, Any]) -> str | None:
    message = commit.get("message")
    if not isinstance(message, str) or not message.strip():
        return None
    return message.strip().split("\n", 1)[0][:200]


def _days_between(earlier: datetime | None, later: datetime) -> int:
    if earlier is None:
        return 0
    delta = _as_utc(later) - _as_utc(earlier)
    return max(0, delta.days)


def _license(repo: dict[str, Any]) -> tuple[str | None, bool]:
    block = repo.get("license")
    if not isinstance(block, dict):
        return None, False
    spdx = str(block.get("spdx_id") or "").strip()
    if spdx.upper() in _UNRECOGNIZED_SPDX:
        name = block.get("name")
        return (str(name) if name else None), False
    return spdx, spdx in OSI_APPROVED_SPDX


def _purpose_source(
    readme: str | None, description: Any, manifest_found: bool
) -> Literal["readme", "description", "manifest", "none"]:
    if readme and len(readme) > 80:
        return "readme"
    if isinstance(description, str) and description.strip():
        return "description"
    if manifest_found:
        return "manifest"
    return "none"


def _manifest_texts(raw: RawPayloads, limit: int) -> dict[str, str]:
    """Decode each fetched manifest, dropping anything undecodable (P7)."""
    out: dict[str, str] = {}
    for path, payload in sorted(raw.contents.items()):
        text = decode_base64_content(payload, limit=limit)
        if text:
            out[path] = text
    return out


def _dependencies(
    manifest_texts: dict[str, str], table: detect.DeprecationTable
) -> tuple[list[Dependency], list[Dependency], Confidence]:
    """Parse manifests and flag deprecated packages against the offline table."""
    collected: dict[str, Dependency] = {}
    for path, text in sorted(manifest_texts.items()):
        for parsed in detect.parse_manifest(path, text):
            key = f"{parsed.ecosystem}:{parsed.name.lower()}"
            if key in collected:
                continue
            flagged = table.lookup(parsed.ecosystem, parsed.name)
            collected[key] = Dependency(
                name=parsed.name,
                spec=parsed.spec,
                ecosystem=parsed.ecosystem,
                deprecated=flagged is not None,
                deprecated_since=flagged.since if flagged else None,
                replacement=flagged.replacement if flagged else None,
                source_url=flagged.source_url if flagged else None,
            )

    dependencies = [collected[k] for k in sorted(collected)]
    deprecated = [d for d in dependencies if d.deprecated]

    if not manifest_texts:
        confidence: Confidence = "low"
    elif not dependencies:
        # A manifest that parsed to nothing is a parse we do not trust (P7).
        confidence = "low"
    elif len(manifest_texts) >= 2 or len(dependencies) >= 5:
        confidence = "high"
    else:
        confidence = "medium"
    return dependencies, deprecated, confidence


class _IssueSignals:
    __slots__ = (
        "available", "sample_size", "unanswered", "answered", "unanswered_ratio",
        "newest_activity_at", "days_since_activity", "oldest_at",
    )

    def __init__(self) -> None:
        self.available = False
        self.sample_size = 0
        self.unanswered = 0
        self.answered = 0
        self.unanswered_ratio = 0.0
        self.newest_activity_at: datetime | None = None
        self.days_since_activity: int | None = None
        self.oldest_at: datetime | None = None


def _issue_signals(payload: list[Any] | None, as_of: datetime) -> _IssueSignals:
    """Maintainer-response signal from the sampled open issues.

    Pull requests appear on the issues endpoint and are excluded: an unanswered
    PR is a different finding from an unanswered issue, and mixing them would
    make the ratio mean neither (design section 4.1, call 9).
    """
    signals = _IssueSignals()
    if payload is None:
        return signals

    issues = [
        item
        for item in payload
        if isinstance(item, dict) and "pull_request" not in item
    ]
    signals.available = True
    signals.sample_size = len(issues)
    if not issues:
        return signals

    for issue in issues:
        comments = issue.get("comments")
        count = int(comments) if isinstance(comments, (int, float)) else 0
        if count == 0:
            signals.unanswered += 1
        else:
            signals.answered += 1

        updated = parse_github_time(issue.get("updated_at"))
        if updated is not None and (
            signals.newest_activity_at is None or updated > signals.newest_activity_at
        ):
            signals.newest_activity_at = updated
        created = parse_github_time(issue.get("created_at"))
        if created is not None and (signals.oldest_at is None or created < signals.oldest_at):
            signals.oldest_at = created

    signals.unanswered_ratio = signals.unanswered / len(issues)
    if signals.newest_activity_at is not None:
        signals.days_since_activity = _days_between(signals.newest_activity_at, as_of)
    return signals
