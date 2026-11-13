"""One typed wrapper per endpoint in the declared call plan (design section 4.1).

The plan is a table in the design document and a constant here, not something
that emerges from control flow. Three properties of every function below:

  - **`per_page` is always explicit** and the response is read once. There is no
    pagination loop in this package and there must never be one (E2).
  - **Tolerated statuses are declared per endpoint**, so "this repository has no
    README" arrives as data rather than as an exception (E10, R-13).
  - **Repository-derived strings are untrusted (E4).** A manifest path comes out
    of the git tree, so `contents()` validates its shape and percent-encodes it.
    It is never used to build a filesystem path and never interpolated anywhere
    but a URL path segment.

`CALL_PLAN` is exported so a test can assert the plan's size against the budget
instead of trusting that the pipeline behaved.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from necropsy.github.client import MAX_PER_PAGE, GitHubClient, GitHubResponse
from necropsy.models.analysis import RepoRef

#: Open-issue sample size. Shown in the UI as the sample it is (P7, R-7.2).
ISSUE_SAMPLE_SIZE = 30

#: Longest manifest path we will request. A path longer than this did not come
#: from a sane repository layout.
MAX_MANIFEST_PATH = 200
MAX_MANIFEST_SEGMENTS = 6


@dataclass(frozen=True)
class PlannedCall:
    """One row of the declared plan."""

    order: int
    key: str
    purpose: str
    optional: bool = False


#: The declared plan: at most 9 calls, leaving 1 of the budget of 10 for a
#: single retry (E2). Adding a row here requires removing one and amending
#: design.md section 4.1 in the same change.
CALL_PLAN: tuple[PlannedCall, ...] = (
    PlannedCall(1, "head_commit", "head SHA and commit date; drives the cache key"),
    PlannedCall(2, "repo", "size, archived, fork, licence, default branch, counts"),
    PlannedCall(3, "languages", "language byte counts for the LOC estimate"),
    PlannedCall(4, "tree", "the whole file list in one call; replaces cloning"),
    PlannedCall(5, "readme", "README excerpt", optional=True),
    PlannedCall(6, "latest_release", "last release tag and date", optional=True),
    PlannedCall(7, "contents_primary", "primary dependency manifest", optional=True),
    PlannedCall(8, "contents_secondary", "secondary manifest", optional=True),
    PlannedCall(9, "open_issues", "maintainer-response signal, sampled", optional=True),
)

PLANNED_CALL_COUNT = len(CALL_PLAN)


# --------------------------------------------------------------------------
# the nine wrappers
# --------------------------------------------------------------------------
def head_commit(client: GitHubClient, ref: RepoRef) -> GitHubResponse:
    """Call 1. Newest commit only. 404 and 409 are mapped errors, not data."""
    return client.get(f"{ref.api_path}/commits", per_page=1)


def repo(client: GitHubClient, ref: RepoRef) -> GitHubResponse:
    """Call 2. Repository metadata, including the guards' size input."""
    return client.get(ref.api_path, per_page=1)


def languages(client: GitHubClient, ref: RepoRef) -> GitHubResponse:
    """Call 3. Byte counts per language. An empty object is a valid answer."""
    return client.get(
        f"{ref.api_path}/languages", per_page=1, tolerate_404=True, tolerate_403=True
    )


def tree(client: GitHubClient, ref: RepoRef, sha: str) -> GitHubResponse:
    """Call 4. The load-bearing call: every structural signal comes from these paths."""
    return client.get(
        f"{ref.api_path}/git/trees/{quote(sha, safe='')}",
        per_page=MAX_PER_PAGE,
        params={"recursive": "1"},
        tolerate_404=True,
    )


def readme(client: GitHubClient, ref: RepoRef) -> GitHubResponse:
    """Call 5. 404 means no README, which is a documentation deficit, not an error."""
    return client.get(
        f"{ref.api_path}/readme", per_page=1, tolerate_404=True, tolerate_403=True
    )


def latest_release(client: GitHubClient, ref: RepoRef) -> GitHubResponse:
    """Call 6. 404 means the project never cut a release. Expected, not a failure."""
    return client.get(
        f"{ref.api_path}/releases/latest", per_page=1, tolerate_404=True, tolerate_403=True
    )


def contents(client: GitHubClient, ref: RepoRef, path: str) -> GitHubResponse:
    """Calls 7 and 8. A dependency manifest, by path, read from the tree listing.

    Raises:
        ValueError: the path is not a plausible in-repository manifest path.
            Callers pass paths that came from the tree, so this is a guard
            against a hostile listing, not against ordinary input (E4).
    """
    safe = _validate_repo_path(path)
    encoded = "/".join(quote(segment, safe="") for segment in safe.split("/"))
    return client.get(
        f"{ref.api_path}/contents/{encoded}",
        per_page=1,
        tolerate_404=True,
        tolerate_403=True,
    )


def open_issues(client: GitHubClient, ref: RepoRef) -> GitHubResponse:
    """Call 9. A sample, newest activity first. Sample size is recorded, not hidden."""
    return client.get(
        f"{ref.api_path}/issues",
        per_page=ISSUE_SAMPLE_SIZE,
        params={"state": "open", "sort": "updated", "direction": "desc"},
        tolerate_404=True,
        tolerate_403=True,
    )


# --------------------------------------------------------------------------
# payload helpers
# --------------------------------------------------------------------------
def decode_base64_content(payload: Any, *, limit: int) -> str | None:
    """Decode a GitHub `contents`/`readme` payload to text, truncated to `limit`.

    Returns None when the payload is absent, not base64, or not decodable text.
    Undecodable content is reported as missing rather than guessed at (P7).
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("content")
    if not isinstance(raw, str) or not raw.strip():
        return None
    if payload.get("encoding") not in (None, "base64"):
        return None
    try:
        decoded = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        return None
    text = decoded.decode("utf-8", errors="replace")
    return text[:limit] if limit >= 0 else text


def _validate_repo_path(path: str) -> str:
    """Whitelist-check a repository-relative path before it reaches a URL."""
    candidate = (path or "").strip().strip("/")
    if not candidate:
        raise ValueError("empty manifest path")
    if len(candidate) > MAX_MANIFEST_PATH:
        raise ValueError("manifest path is implausibly long")
    if "\\" in candidate or "//" in candidate:
        raise ValueError("manifest path contains an unexpected separator")
    segments = candidate.split("/")
    if len(segments) > MAX_MANIFEST_SEGMENTS:
        raise ValueError("manifest path is nested more deeply than expected")
    for segment in segments:
        if segment in {"", ".", ".."}:
            raise ValueError("manifest path contains a traversal segment")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in segment):
            raise ValueError("manifest path contains a control character")
    return candidate
