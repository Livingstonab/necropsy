"""The declared call plan, executed in order, with the guards in between (section 4.2).

The plan is data, not control flow: `endpoints.CALL_PLAN` declares it and this
module walks it. That is what makes "at most 9 calls, 1 held in reserve" a claim
a test can check rather than a promise about how the code reads (E2).

Guard ordering is the other job here, and it is deliberate (E10). A repository
that fails the size cap costs 4 calls and zero LLM tokens, because the cap is
checked the moment the metadata lands and again the moment the tree lands, not
after everything has been fetched. Spending budget to discover you should not
have spent budget is the failure mode this ordering exists to prevent.

Raw payloads are returned verbatim and stored verbatim, so re-normalizing or
re-scoring after a weight change costs zero API calls (E3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from necropsy.config import Config, load_config
from necropsy.errors import RepoEmpty, RepoTooLarge
from necropsy.github import endpoints
from necropsy.github.client import BudgetScope
from necropsy.models.analysis import RepoRef

log = logging.getLogger("necropsy.analysis.fetch")

#: Called at each stage boundary; raises AnalysisTimeout when the budget is gone.
DeadlineCheck = Callable[[], None]


def _no_deadline() -> None:
    return None


@dataclass(frozen=True)
class HeadCommit:
    """Call 1's answer: the SHA the cache key is built from."""

    sha: str
    committed_at: datetime
    message: str | None
    payload: Any

    @property
    def short_sha(self) -> str:
        return self.sha[:7]


@dataclass(frozen=True)
class RawPayloads:
    """Every raw GitHub payload, keyed by endpoint (design section 3).

    Stored as `raw_github_json`. Keeping the raw bodies is what makes a schema
    bump cheap and the cache-invariance property testable on real data (E3).
    """

    data: dict[str, Any] = field(default_factory=dict)
    calls_used: int = 0
    tolerated: tuple[str, ...] = ()

    # -- round trip ---------------------------------------------------------
    def to_mapping(self) -> dict[str, Any]:
        return {
            "payloads": self.data,
            "calls_used": self.calls_used,
            "tolerated": list(self.tolerated),
        }

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> RawPayloads:
        """Rebuild from a stored `raw_github_json` blob. Zero API calls (E3)."""
        payloads = mapping.get("payloads")
        if not isinstance(payloads, dict):
            # Tolerate a bare endpoint map, which is what a hand-written
            # fixture is most naturally written as.
            payloads = {k: v for k, v in mapping.items() if k not in
                        {"calls_used", "tolerated"}}
        return cls(
            data=payloads,
            calls_used=int(mapping.get("calls_used", 0) or 0),
            tolerated=tuple(mapping.get("tolerated", []) or ()),
        )

    # -- accessors ----------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def head_commit(self) -> Any:
        return self.data.get("head_commit")

    @property
    def repo(self) -> dict[str, Any]:
        value = self.data.get("repo")
        return value if isinstance(value, dict) else {}

    @property
    def languages(self) -> dict[str, int]:
        value = self.data.get("languages")
        if not isinstance(value, dict):
            return {}
        return {
            str(k): int(v)
            for k, v in value.items()
            if isinstance(v, (int, float)) and v > 0
        }

    @property
    def tree(self) -> dict[str, Any]:
        value = self.data.get("tree")
        return value if isinstance(value, dict) else {}

    @property
    def tree_paths(self) -> list[str]:
        """Blob paths only. Directory entries are not files (E4: strings only)."""
        entries = self.tree.get("tree")
        if not isinstance(entries, list):
            return []
        return [
            str(entry.get("path"))
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and entry.get("path")
        ]

    @property
    def tree_truncated(self) -> bool:
        return bool(self.tree.get("truncated"))

    @property
    def readme(self) -> dict[str, Any] | None:
        value = self.data.get("readme")
        return value if isinstance(value, dict) else None

    @property
    def latest_release(self) -> dict[str, Any] | None:
        value = self.data.get("latest_release")
        return value if isinstance(value, dict) else None

    @property
    def contents(self) -> dict[str, Any]:
        value = self.data.get("contents")
        return value if isinstance(value, dict) else {}

    @property
    def open_issues(self) -> list[Any] | None:
        value = self.data.get("open_issues")
        return value if isinstance(value, list) else None


# --------------------------------------------------------------------------
# call 1: identity
# --------------------------------------------------------------------------
def resolve_head(scope: BudgetScope, ref: RepoRef) -> HeadCommit:
    """Call 1. Resolve the head SHA, which the cache key is defined by (E3).

    This is the one call a cache hit pays for: the key is
    `(url, sha, schema_version)`, so the SHA must be known to answer the cache
    question at all (design section 2.3).
    """
    response = endpoints.head_commit(scope.client, ref)
    payload = response.body
    if not isinstance(payload, list) or not payload:
        raise RepoEmpty(
            "That repository has no commits, so there is nothing to examine.",
            details={"repo": ref.display_url},
        )

    newest = payload[0]
    commit = newest.get("commit", {}) if isinstance(newest, dict) else {}
    committer = commit.get("committer") or commit.get("author") or {}
    raw_date = committer.get("date") if isinstance(committer, dict) else None
    sha = str(newest.get("sha", "")).strip() if isinstance(newest, dict) else ""
    if not sha or not raw_date:
        raise RepoEmpty(
            "GitHub returned a commit without a SHA or a date, so this repository "
            "cannot be identified at a point in time.",
            details={"repo": ref.display_url},
        )

    return HeadCommit(
        sha=sha,
        committed_at=parse_github_time(raw_date) or datetime.fromisoformat(raw_date),
        message=(commit.get("message") or "").split("\n", 1)[0][:200] or None,
        payload=newest,
    )


# --------------------------------------------------------------------------
# calls 2-9
# --------------------------------------------------------------------------
def fetch(
    ref: RepoRef,
    scope: BudgetScope,
    head: HeadCommit,
    *,
    config: Config | None = None,
    deadline: DeadlineCheck = _no_deadline,
) -> RawPayloads:
    """Walk the declared plan, applying the guards in the order design 4.2 sets.

    Raises:
        RepoTooLarge: the size cap, the file cap, or a truncated tree. Spends at
            most 4 calls and writes nothing (E10).
        AnalysisTimeout: via `deadline`, checked at every stage boundary.
    """
    cfg = config or load_config()
    data: dict[str, Any] = {"head_commit": head.payload}
    tolerated: list[str] = []

    def record(key: str, response: Any) -> Any:
        if response.absent:
            tolerated.append(f"{key}:{response.status}")
            log.debug("%s unavailable (status %s); continuing", key, response.status)
            return None
        return response.body

    # -- call 2: metadata, then the size guard -----------------------------
    deadline()
    repo_payload = endpoints.repo(scope.client, ref)
    data["repo"] = repo_payload.body if isinstance(repo_payload.body, dict) else {}
    size_kb = int(data["repo"].get("size") or 0)
    if size_kb > cfg.max_repo_size_kb:
        raise RepoTooLarge(
            "That repository is larger than the size NECROPSY will examine.",
            details={
                "size_kb": size_kb,
                "max_repo_size_kb": cfg.max_repo_size_kb,
                "github_calls_used": scope.calls_used,
            },
        )

    # -- call 3: languages -------------------------------------------------
    deadline()
    data["languages"] = record("languages", endpoints.languages(scope.client, ref)) or {}

    # -- call 4: the tree, then the file and truncation guards -------------
    deadline()
    tree_response = endpoints.tree(scope.client, ref, head.sha)
    data["tree"] = record("tree", tree_response) or {}
    raw = RawPayloads(data=data)
    if raw.tree_truncated:
        raise RepoTooLarge(
            "GitHub truncated this repository's file listing, so NECROPSY cannot "
            "see the whole tree and will not guess at the rest.",
            details={
                "reason": "tree_truncated",
                "github_calls_used": scope.calls_used,
            },
        )
    file_count = len(raw.tree_paths)
    if file_count > cfg.max_repo_files:
        raise RepoTooLarge(
            "That repository has more files than NECROPSY will examine.",
            details={
                "file_count": file_count,
                "max_repo_files": cfg.max_repo_files,
                "github_calls_used": scope.calls_used,
            },
        )

    # -- call 5: README ----------------------------------------------------
    deadline()
    data["readme"] = record("readme", endpoints.readme(scope.client, ref))

    # -- call 6: latest release (404 is the common, expected case) ---------
    deadline()
    data["latest_release"] = record(
        "latest_release", endpoints.latest_release(scope.client, ref)
    )

    # -- calls 7 and 8: dependency manifests, chosen from the tree ---------
    deadline()
    data["contents"] = _fetch_manifests(ref, scope, raw.tree_paths, tolerated)

    # -- call 9: the open-issue sample -------------------------------------
    deadline()
    data["open_issues"] = record("open_issues", endpoints.open_issues(scope.client, ref))

    return RawPayloads(
        data=data, calls_used=scope.calls_used, tolerated=tuple(tolerated)
    )


def _fetch_manifests(
    ref: RepoRef,
    scope: BudgetScope,
    tree_paths: list[str],
    tolerated: list[str],
) -> dict[str, Any]:
    """Read at most two manifests: the primary, and one from a second ecosystem.

    Imported here rather than at module scope to keep the dependency between
    fetch and detect one-directional and obvious.
    """
    from necropsy.analysis.detect import _find_manifests  # noqa: PLC0415

    candidates, _ecosystems = _find_manifests(tree_paths)
    fetched: dict[str, Any] = {}
    for index, path in enumerate(candidates[:2]):
        key = "contents_primary" if index == 0 else "contents_secondary"
        try:
            response = endpoints.contents(scope.client, ref, path)
        except ValueError:
            # A path the tree offered but that is not a plausible manifest path.
            # Skipped, not fatal: repository content is untrusted data (E4).
            log.warning("skipping an implausible manifest path from the tree listing")
            tolerated.append(f"{key}:rejected_path")
            continue
        if response.absent:
            tolerated.append(f"{key}:{response.status}")
            continue
        fetched[path] = response.body
    return fetched


def parse_github_time(value: Any) -> datetime | None:
    """Parse an ISO 8601 timestamp from GitHub, tolerating the `Z` suffix."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
