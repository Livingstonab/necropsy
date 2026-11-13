"""URL canonicalization: table test over every R-4 form, plus the identity
property (R-4.1 - R-4.5, T-1.5).

Offline by construction — `canonicalize` is pure string work, so there is
nothing here to mock (E11). The no-network claim is asserted directly by
patching `httpx` out from under the module.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from hypothesis import given, settings

from necropsy.errors import ErrorCode, InvalidUrl, NecropsyError, UnsupportedHost
from necropsy.github.urls import MAX_RAW_LENGTH, canonicalize
from tests.strategies import (
    equivalent_url_forms,
    non_github_hosts,
    repo_names,
    repo_owners,
    unsupported_url_forms,
)

# --------------------------------------------------------------------------
# R-4.1 / R-4.2 — accepted forms
# --------------------------------------------------------------------------
ACCEPTED: list[tuple[str, str, str, str]] = [
    # (label, raw input, expected owner, expected name)
    ("canonical https", "https://github.com/Owner/Repo", "Owner", "Repo"),
    ("http scheme", "http://github.com/Owner/Repo", "Owner", "Repo"),
    ("www prefix", "https://www.github.com/Owner/Repo", "Owner", "Repo"),
    ("www, no scheme", "www.github.com/Owner/Repo", "Owner", "Repo"),
    ("schemeless host", "github.com/Owner/Repo", "Owner", "Repo"),
    ("trailing slash", "https://github.com/Owner/Repo/", "Owner", "Repo"),
    ("double trailing slash", "https://github.com/Owner/Repo//", "Owner", "Repo"),
    (".git suffix", "https://github.com/Owner/Repo.git", "Owner", "Repo"),
    (".git plus slash", "https://github.com/Owner/Repo.git/", "Owner", "Repo"),
    (".GIT uppercase", "https://github.com/Owner/Repo.GIT", "Owner", "Repo"),
    ("tree ref", "https://github.com/Owner/Repo/tree/main", "Owner", "Repo"),
    ("tree nested ref", "https://github.com/Owner/Repo/tree/release/1.x", "Owner", "Repo"),
    ("blob ref and path", "https://github.com/Owner/Repo/blob/main/src/a.py", "Owner", "Repo"),
    ("query string", "https://github.com/Owner/Repo?tab=readme", "Owner", "Repo"),
    ("fragment", "https://github.com/Owner/Repo#install", "Owner", "Repo"),
    ("query and fragment", "https://github.com/Owner/Repo/?a=1&b=2#x", "Owner", "Repo"),
    ("schemeless query", "github.com/Owner/Repo?a=1", "Owner", "Repo"),
    ("issues sub-route", "https://github.com/Owner/Repo/issues/42", "Owner", "Repo"),
    ("pull sub-route", "https://github.com/Owner/Repo/pull/7/files", "Owner", "Repo"),
    ("releases sub-route", "https://github.com/Owner/Repo/releases/tag/v1.0", "Owner", "Repo"),
    ("commit sub-route", "https://github.com/Owner/Repo/commit/deadbeef", "Owner", "Repo"),
    ("port", "https://github.com:443/Owner/Repo", "Owner", "Repo"),
    ("surrounding whitespace", "   https://github.com/Owner/Repo\n", "Owner", "Repo"),
    ("bare form", "Owner/Repo", "Owner", "Repo"),
    ("bare form, leading slash", "/Owner/Repo", "Owner", "Repo"),
    ("bare form, .git", "Owner/Repo.git", "Owner", "Repo"),
    ("bare form, trailing slash", "Owner/Repo/", "Owner", "Repo"),
    ("dotted repo name", "github.com/Owner/repo.js", "Owner", "repo.js"),
    ("underscored repo name", "github.com/Owner/some_repo", "Owner", "some_repo"),
    ("hyphenated owner", "github.com/some-org/repo", "some-org", "repo"),
    ("single-char owner", "github.com/a/b", "a", "b"),
    ("39-char owner", f"github.com/{'a' * 39}/repo", "a" * 39, "repo"),
    ("100-char name", f"github.com/owner/{'r' * 100}", "owner", "r" * 100),
]


@pytest.mark.parametrize(
    ("raw", "owner", "name"),
    [pytest.param(raw, owner, name, id=label) for label, raw, owner, name in ACCEPTED],
)
def test_accepted_forms_canonicalize(raw: str, owner: str, name: str) -> None:
    ref = canonicalize(raw)
    assert (ref.owner, ref.name) == (owner, name)
    # Display keeps the pasted case; the cache side is lowercased (R-4.1).
    assert ref.display_url == f"github.com/{owner}/{name}"
    assert ref.canonical_url == f"github.com/{owner}/{name}".lower()
    # An accepted identity names exactly one host, whatever was pasted (R-4.3).
    assert ref.host == "github.com"


# --------------------------------------------------------------------------
# R-4.3 — other hosts
# --------------------------------------------------------------------------
UNSUPPORTED: list[tuple[str, str, str]] = [
    ("gitlab", "https://gitlab.com/owner/repo", "gitlab.com"),
    ("bitbucket", "https://bitbucket.org/owner/repo", "bitbucket.org"),
    ("gitlab schemeless", "gitlab.com/owner/repo", "gitlab.com"),
    ("codeberg", "http://codeberg.org/owner/repo", "codeberg.org"),
    ("gist", "https://gist.github.com/owner/repo", "gist.github.com"),
    ("api host", "https://api.github.com/repos/owner/repo", "api.github.com"),
    ("lookalike suffix", "https://github.com.evil.test/owner/repo", "github.com.evil.test"),
    ("lookalike prefix", "https://notgithub.com/owner/repo", "notgithub.com"),
    ("subdomain", "https://pages.github.com/owner/repo", "pages.github.com"),
    ("ip host", "http://127.0.0.1/owner/repo", "127.0.0.1"),
    ("localhost with port", "localhost:8080/owner/repo", "localhost"),
    ("case-folded host", "HTTPS://GITLAB.COM/Owner/Repo", "gitlab.com"),
]


@pytest.mark.parametrize(
    ("raw", "host"),
    [pytest.param(raw, host, id=label) for label, raw, host in UNSUPPORTED],
)
def test_other_hosts_rejected_with_host_echoed(raw: str, host: str) -> None:
    with pytest.raises(UnsupportedHost) as caught:
        canonicalize(raw)
    err = caught.value
    assert err.code is ErrorCode.UNSUPPORTED_HOST
    assert err.details["host"] == host  # R-4.3 echoes the parsed host
    assert err.retryable is False


# --------------------------------------------------------------------------
# R-4.4 — unparseable input
# --------------------------------------------------------------------------
INVALID: list[tuple[str, str]] = [
    ("empty", ""),
    ("whitespace only", "   "),
    ("inner whitespace", "github.com/owner/re po"),
    ("garbage words", "not a url"),
    ("single word", "repo"),
    ("owner only", "https://github.com/owner"),
    ("owner only, slash", "https://github.com/owner/"),
    ("host only", "https://github.com"),
    ("host root", "https://github.com/"),
    ("ssh scheme", "ssh://github.com/owner/repo"),
    ("git scheme", "git://github.com/owner/repo"),
    ("file scheme", "file:///etc/passwd"),
    ("javascript scheme", "javascript://github.com/owner/repo"),
    ("no host in url", "https:///owner/repo"),
    ("unknown sub-route", "https://github.com/owner/repo/settings/secrets"),
    ("dot owner", "github.com/./repo"),
    ("dotdot traversal", "https://github.com/owner/../../etc/passwd"),
    ("dot repo name", "github.com/owner/."),
    ("dotdot repo name", "github.com/owner/.."),
    ("bare .git repo name", "github.com/owner/.git"),
    ("owner with underscore", "github.com/bad_owner/repo"),
    ("owner leading hyphen", "github.com/-owner/repo"),
    ("owner trailing hyphen", "github.com/owner-/repo"),
    ("owner double hyphen", "github.com/ow--ner/repo"),
    ("owner too long", f"github.com/{'a' * 40}/repo"),
    ("name too long", f"github.com/owner/{'r' * 101}"),
    ("name with colon", "github.com/owner/re:po"),
    ("name with percent", "github.com/owner/re%2fpo"),
    ("unicode owner", "github.com/ownér/repo"),
    ("nul byte", "github.com/owner/re\x00po"),
    ("over-length input", "https://github.com/owner/" + "r" * MAX_RAW_LENGTH),
]


@pytest.mark.parametrize(
    "raw", [pytest.param(raw, id=label) for label, raw in INVALID]
)
def test_unparseable_input_rejected(raw: str) -> None:
    with pytest.raises(InvalidUrl) as caught:
        canonicalize(raw)
    assert caught.value.code is ErrorCode.INVALID_URL
    assert caught.value.retryable is False


def test_non_string_input_is_invalid_url() -> None:
    """A JSON body can carry a non-string; that is bad input, not a crash."""
    with pytest.raises(InvalidUrl):
        canonicalize(cast(Any, None))


@pytest.mark.parametrize(
    "raw", [raw for _, raw in INVALID] + [raw for _, raw, _ in UNSUPPORTED]
)
def test_every_rejection_is_a_necropsy_error(raw: str) -> None:
    """Rejections stay inside the closed taxonomy, so the shared envelope
    always applies (E8, E10)."""
    with pytest.raises(NecropsyError) as caught:
        canonicalize(raw)
    envelope = caught.value.to_envelope()
    assert envelope["ok"] is False
    assert envelope["error"]["code"] in {"INVALID_URL", "UNSUPPORTED_HOST"}
    assert envelope["error"]["correlation_id"]


def test_rejection_details_never_echo_the_whole_input() -> None:
    """Untrusted input is not reflected wholesale into an error payload (E4)."""
    hostile = "github.com/owner/repo/" + "A" * 500
    with pytest.raises(InvalidUrl) as caught:
        canonicalize(hostile)
    rendered = str(caught.value.details)
    assert len(rendered) < 200


# --------------------------------------------------------------------------
# Zero network calls (R-4.4, E4)
# --------------------------------------------------------------------------
def test_canonicalize_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard 1 runs before any budget is spent, so it must not touch the socket."""
    import socket

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("canonicalize() attempted a network call")

    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "getaddrinfo", explode)
    monkeypatch.setattr(socket, "create_connection", explode)

    assert canonicalize("https://github.com/Owner/Repo").name == "Repo"
    with pytest.raises(InvalidUrl):
        canonicalize("not a url")
    with pytest.raises(UnsupportedHost):
        canonicalize("https://gitlab.com/o/r")


def test_urls_module_imports_no_io_libraries() -> None:
    """`github/urls.py` is pure: it may not reach for a client, a socket, a
    subprocess or the filesystem."""
    import necropsy.github.urls as urls

    forbidden = {"httpx", "requests", "socket", "subprocess", "os", "pathlib", "sqlite3"}
    assert forbidden.isdisjoint(vars(urls))


# --------------------------------------------------------------------------
# R-4.1 / R-4.5 — identity property across equivalent forms
#
# The strategies live in `tests/strategies.py` so later tasks reuse one
# definition of a valid repository identity (E11, R-20.9).
# --------------------------------------------------------------------------
owners = repo_owners()
names = repo_names()


@settings(max_examples=200)
@given(owner=owners, name=names)
def test_equivalent_forms_share_one_identity(owner: str, name: str) -> None:
    forms = equivalent_url_forms(owner, name)
    refs = [canonicalize(form) for form in forms]
    first = refs[0]
    for form, ref in zip(forms, refs, strict=True):
        assert ref == first, f"{form} canonicalized to {ref}, expected {first}"
        assert ref.canonical_url == first.canonical_url
        assert ref.slug == first.slug
        assert ref.api_path == first.api_path


@settings(max_examples=200)
@given(host=non_github_hosts(), owner=owners, name=names)
def test_no_spelling_smuggles_a_foreign_host_through(
    host: str, owner: str, name: str
) -> None:
    """R-4.3 holds for every accepted *shape*, not just the canonical one: a
    repository path on another host is refused with the host echoed back."""
    for form in unsupported_url_forms(host, owner, name):
        with pytest.raises(UnsupportedHost) as caught:
            canonicalize(form)
        assert caught.value.details["host"] == host, form


@settings(max_examples=200)
@given(owner=owners, name=names)
def test_case_differences_share_a_cache_identity(owner: str, name: str) -> None:
    """R-4.5: case-only differences must land on the same cache key input,
    while display case survives untouched."""
    lower = canonicalize(f"https://github.com/{owner.lower()}/{name.lower()}")
    upper = canonicalize(f"https://GITHUB.COM/{owner.upper()}/{name.upper()}")
    assert lower.canonical_url == upper.canonical_url
    assert lower.slug == upper.slug
    assert upper.display_url == f"github.com/{owner.upper()}/{name.upper()}"


@settings(max_examples=200)
@given(owner=owners, name=names)
def test_canonical_output_is_a_fixed_point(owner: str, name: str) -> None:
    """Canonicalizing an already-canonical identity changes nothing, which is
    what lets stored URLs be re-parsed without drift (E3)."""
    ref = canonicalize(f"https://github.com/{owner}/{name}")
    assert canonicalize(ref.display_url) == ref
    assert canonicalize(ref.html_url) == ref
    assert canonicalize(canonicalize(ref.canonical_url).canonical_url).canonical_url == (
        ref.canonical_url
    )
