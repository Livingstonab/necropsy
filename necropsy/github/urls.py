"""Repository-identity parsing (R-4, design.md section 4.2 guard 1).

Pure string work. `canonicalize()` makes zero network calls and performs zero
filesystem access, which is what lets it run as guard 1 before any budget is
spent (E2, E10 ordering). Input is untrusted (E4): the parser is a whitelist,
never a best-effort fixup, and no parsed segment is ever used to build a path
or a command.

Accepted forms (R-4.1, R-4.2):

    owner/repo                              bare form
    github.com/owner/repo                   schemeless
    https://github.com/owner/repo           canonical
    http://github.com/owner/repo            either scheme
    https://www.github.com/owner/repo       www prefix
    https://github.com/owner/repo/          trailing slash
    https://github.com/owner/repo.git       .git suffix
    https://github.com/owner/repo/tree/main          tree ref
    https://github.com/owner/repo/blob/main/a/b.py   blob ref + path
    https://github.com/owner/repo?x=1#frag           query and fragment

Rejected:

    any host other than github.com / www.github.com  -> UnsupportedHost
    anything else                                    -> InvalidUrl
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from necropsy.errors import InvalidUrl, UnsupportedHost
from necropsy.models.analysis import RepoRef

# A paste longer than this is not a repository URL. Bounded before any regex
# runs, so a hostile input cannot buy more work than one length check.
MAX_RAW_LENGTH = 2048

#: The only hosts NECROPSY reads (P3/E4: exactly one egress destination for
#: repository data). Matched exactly after lowercasing — `github.com.evil.test`
#: and `gist.github.com` are both other hosts.
GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: GitHub account names: ASCII alphanumerics and single inner hyphens, 1-39
#: characters. Notably no dots, which is what lets a dotted first segment be
#: read as a hostname rather than an owner.
_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

#: Repository names: ASCII alphanumerics, hyphen, underscore, dot; 1-100 chars.
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

#: Repository names that are not names.
_RESERVED_NAMES = frozenset({".", "..", ".git"})

#: Third path segments that still describe the same repository, so they are
#: discarded rather than rejected. Anything else after `owner/repo` means the
#: input is not a repository reference (R-4.4).
_REPO_SUBROUTES = frozenset(
    {
        "actions",
        "blame",
        "blob",
        "branches",
        "commit",
        "commits",
        "compare",
        "discussions",
        "graphs",
        "issues",
        "labels",
        "milestones",
        "network",
        "pull",
        "pulls",
        "raw",
        "releases",
        "tags",
        "tree",
        "wiki",
    }
)


def canonicalize(raw: str) -> RepoRef:
    """Resolve any accepted spelling of a GitHub repository to one identity.

    Case is preserved for display; `RepoRef.canonical_url` lowercases for the
    cache key, so two spellings that differ only in case share a cache entry
    (R-4.1, R-4.5, E3).

    Raises:
        UnsupportedHost: the reference names a host that is not github.com.
        InvalidUrl: the input is not parseable as a repository reference.
    """
    if not isinstance(raw, str):
        # A JSON body can carry anything; a non-string is bad input, not a bug.
        raise InvalidUrl("A repository URL must be a string.")
    text = raw.strip()
    if not text:
        raise InvalidUrl("No repository URL was provided.")
    if len(text) > MAX_RAW_LENGTH:
        raise InvalidUrl(
            "That input is too long to be a repository URL.",
            details={"length": len(text)},
        )
    if any(ch.isspace() for ch in text):
        raise InvalidUrl("A repository URL cannot contain whitespace.")

    host, path = _split_host_and_path(text)
    if host is not None and host not in GITHUB_HOSTS:
        raise UnsupportedHost(
            "NECROPSY reads repositories on github.com only.",
            details={"host": host},
        )

    owner, name = _repo_segments(path)
    return RepoRef(owner=owner, name=name)


def _split_host_and_path(text: str) -> tuple[str | None, str]:
    """Return `(host, path)`, where host is None for the bare `owner/repo` form.

    Query strings and fragments are dropped here (R-4.1).
    """
    if "://" in text:
        parts = urlsplit(text)
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            raise InvalidUrl(
                "Only http and https repository URLs are accepted.",
                details={"scheme": parts.scheme.lower()},
            )
        try:
            hostname = parts.hostname
        except ValueError as exc:  # malformed authority, e.g. a bad port
            raise InvalidUrl("That URL could not be parsed.") from exc
        if not hostname:
            raise InvalidUrl("That URL names no host.")
        return hostname.lower(), parts.path

    # Schemeless. Split manually: urlsplit would read `github.com/o/r` as a
    # path, and `o/r:1` as a scheme.
    head, _, tail = text.partition("/")
    path_part = _strip_query_and_fragment(tail)
    if "." in head or ":" in head:
        # A dot or a port cannot occur in an owner name, so this is a host.
        host = head.split(":", 1)[0].lower()
        if not host:
            raise InvalidUrl("That URL names no host.")
        return host, path_part
    return None, f"{head}/{path_part}"


def _strip_query_and_fragment(path: str) -> str:
    for sep in ("?", "#"):
        path = path.split(sep, 1)[0]
    return path


def _repo_segments(path: str) -> tuple[str, str]:
    """Pull `owner` and `repo` out of a path, discarding known sub-routes."""
    segments = [s for s in _strip_query_and_fragment(path).split("/") if s]
    if len(segments) < 2:
        raise InvalidUrl(
            "A repository reference needs both an owner and a repository name.",
            details={"segments": len(segments)},
        )
    owner, name = segments[0], segments[1]
    extra = segments[2:]
    if extra and extra[0].lower() not in _REPO_SUBROUTES:
        raise InvalidUrl(
            "That path does not point at a repository.",
            details={"unexpected_segment": extra[0][:64]},
        )

    name = _strip_git_suffix(name)
    if not _OWNER_RE.match(owner):
        raise InvalidUrl("That owner name is not a valid GitHub account name.")
    if not _NAME_RE.match(name) or name in _RESERVED_NAMES:
        raise InvalidUrl("That repository name is not a valid GitHub repository name.")
    return owner, name


def _strip_git_suffix(name: str) -> str:
    if len(name) > 4 and name.lower().endswith(".git"):
        return name[:-4]
    return name
