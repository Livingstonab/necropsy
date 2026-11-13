"""GitHubClient: budget, ledger, ETags, the single retry (R-14, E2, E11).

Every test here is offline. `respx` intercepts httpx, so no socket is opened and
no key is needed. The token used below is a literal placeholder, not a secret,
and a test asserts it never escapes into a repr.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from necropsy.errors import (
    CallBudgetExceeded,
    ErrorCode,
    RateLimited,
    RepoEmpty,
    RepoInaccessible,
    RepoNotFound,
    UpstreamError,
)
from necropsy.github.client import (
    GITHUB_CALL_BUDGET,
    RATE_LIMIT_FLOOR,
    GitHubClient,
    build_http_client,
)
from necropsy.store import ledger
from necropsy.store.db import Database

REPO_PATH = "/repos/octocat/hello-world"
COMMITS_PATH = f"{REPO_PATH}/commits"
PLACEHOLDER_TOKEN = "placeholder-not-a-real-token"


def _headers(
    *,
    remaining: int = 4_999,
    limit: int = 5_000,
    used: int = 1,
    reset: int = 2_000_000_000,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Used": str(used),
        "X-RateLimit-Reset": str(reset),
    }
    if extra:
        headers.update(extra)
    return headers


class RecordingSleep:
    """Stands in for time.sleep so a honored Retry-After costs no wall clock."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


@pytest.fixture
def sleeper() -> RecordingSleep:
    return RecordingSleep()


@pytest.fixture
def client(db: Database, sleeper: RecordingSleep) -> Iterator[GitHubClient]:
    """A keyless client — the configuration the app must always work in (R-14.4)."""
    with GitHubClient(db, sleep=sleeper) as c:
        yield c


# -- construction ----------------------------------------------------------
def test_works_with_no_github_token(db: Database) -> None:
    """No GITHUB_TOKEN: the client constructs and sends no Authorization header."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}").respond(
            200, json={"full_name": "octocat/Hello-World"}, headers=_headers()
        )
        with GitHubClient(db) as c:
            assert c.token_present is False
            result = c.get(REPO_PATH, per_page=1)

    assert result.body == {"full_name": "octocat/Hello-World"}
    assert "Authorization" not in route.calls[0].request.headers


def test_token_is_sent_as_bearer_and_never_rendered(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", PLACEHOLDER_TOKEN)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}").respond(
            200, json={}, headers=_headers()
        )
        with GitHubClient(db) as c:
            c.get(REPO_PATH, per_page=1)
            rendered = f"{c!r} {c!s}"

    assert route.calls[0].request.headers["Authorization"] == f"Bearer {PLACEHOLDER_TOKEN}"
    assert c.token_present is True
    assert PLACEHOLDER_TOKEN not in rendered
    assert "token_present=True" in rendered


def test_timeouts_are_five_connect_ten_read() -> None:
    http = build_http_client(None)
    try:
        assert http.timeout.connect == 5.0
        assert http.timeout.read == 10.0
    finally:
        http.close()


# -- budget (E2, R-14.1) ---------------------------------------------------
def test_eleventh_call_raises_call_budget_exceeded(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(200, json={}, headers=_headers())

        for _ in range(GITHUB_CALL_BUDGET):
            client.get(REPO_PATH, per_page=1)
        assert client.calls_used == 10
        assert client.calls_remaining == 0

        with pytest.raises(CallBudgetExceeded) as caught:
            client.get(REPO_PATH, per_page=1)

    assert caught.value.code is ErrorCode.CALL_BUDGET_EXCEEDED
    assert caught.value.retryable is False
    assert caught.value.details["calls_used"] == 10
    # The refused attempt spent nothing extra: the counter is the ceiling.
    assert client.calls_used == 10


def test_env_cannot_raise_the_budget_above_ten(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_CALL_BUDGET", "500")
    with GitHubClient(db) as c:
        assert c.budget == GITHUB_CALL_BUDGET


def test_begin_analysis_resets_the_counter(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(200, json={}, headers=_headers())
        client.get(REPO_PATH, per_page=1)
        assert client.calls_used == 1
        client.begin_analysis()
        assert client.calls_used == 0


# -- rate-limit floor pre-check (R-14.3) -----------------------------------
def test_floor_precheck_refuses_with_zero_calls(db: Database) -> None:
    with db.transaction() as conn:
        ledger.record(
            conn,
            ledger.RateLimitObservation(
                endpoint=REPO_PATH, status=200, limit=60, remaining=RATE_LIMIT_FLOOR - 1, used=49
            ),
        )

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}").respond(200, json={})
        with GitHubClient(db) as c, pytest.raises(RateLimited) as caught:
            c.begin_analysis()

    assert caught.value.code is ErrorCode.RATE_LIMITED
    assert caught.value.details["calls_spent"] == 0
    assert caught.value.details["rate_limit_remaining"] == RATE_LIMIT_FLOOR - 1
    assert route.call_count == 0


def test_floor_ignores_a_reading_taken_under_different_auth(db: Database) -> None:
    """A low reading recorded with no token must not block a now-tokened client.

    This is the real-world trap: a user runs unauthenticated, burns their 60,
    then adds a token. The old "4 remaining" reading was taken at the 60 limit
    and says nothing about the new 5,000 limit, so the floor check ignores it.
    """
    from necropsy.config import Config

    with db.transaction() as conn:
        ledger.record(
            conn,
            ledger.RateLimitObservation(
                endpoint=REPO_PATH, status=200, limit=60, remaining=4, used=56,
                token_present=False,  # recorded while unauthenticated
            ),
        )
    # A client that now holds a token must not be blocked by that stale reading.
    tokened = GitHubClient(db, Config(github_token="ghp_fresh"))
    tokened.check_rate_limit_floor()  # must not raise
    tokened.close()


def test_floor_ignores_a_reading_whose_window_has_reset(db: Database) -> None:
    """A low reading whose reset time is in the past is spent; ignore it."""
    from datetime import UTC, datetime, timedelta

    past = datetime.now(UTC) - timedelta(minutes=5)
    with db.transaction() as conn:
        ledger.record(
            conn,
            ledger.RateLimitObservation(
                endpoint=REPO_PATH, status=200, limit=60, remaining=2, used=58,
                reset_at=past, token_present=False,
            ),
        )
    client = GitHubClient(db)  # also no token, so auth matches
    client.check_rate_limit_floor()  # must not raise: the window has reset
    client.close()


def test_floor_precheck_allows_an_empty_ledger(client: GitHubClient) -> None:
    """No observation is not evidence of exhaustion; the counter still caps it."""
    client.begin_analysis()
    assert client.calls_used == 0


def test_floor_precheck_allows_healthy_remaining(db: Database) -> None:
    with db.transaction() as conn:
        ledger.record(
            conn,
            ledger.RateLimitObservation(
                endpoint=REPO_PATH, status=200, limit=5_000, remaining=4_321, used=679
            ),
        )
    with GitHubClient(db) as c:
        c.begin_analysis()
        assert c.calls_used == 0


# -- ledger writes (R-14.2) ------------------------------------------------
def test_every_response_writes_a_ledger_row(client: GitHubClient, db: Database) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{COMMITS_PATH}").respond(
            200,
            json=[{"sha": "abc"}],
            headers=_headers(remaining=4_998, used=2, reset=1_900_000_000),
        )
        client.get(COMMITS_PATH, per_page=1)
        client.get(COMMITS_PATH, per_page=1)

    conn = db.connect()
    assert ledger.count_observations(conn) == 2
    state = ledger.latest(conn)
    assert state is not None
    assert (state.endpoint, state.status) == (COMMITS_PATH, 200)
    assert (state.limit, state.remaining, state.used) == (5_000, 4_998, 2)
    assert state.reset_at is not None
    assert state.token_present is False


def test_rate_limited_response_also_writes_a_ledger_row(
    client: GitHubClient, db: Database
) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(
            403,
            json={"message": "API rate limit exceeded for 203.0.113.1."},
            headers=_headers(remaining=0, used=60, limit=60),
        )
        with pytest.raises(RateLimited):
            client.get(REPO_PATH, per_page=1)

    state = ledger.latest(db.connect())
    assert state is not None
    assert state.status == 403
    assert state.remaining == 0


# -- ETag / 304 (R-14.7) ---------------------------------------------------
def test_304_returns_the_stored_body(client: GitHubClient, db: Database) -> None:
    body = {"full_name": "octocat/Hello-World", "size": 108}
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(
            200, json=body, headers=_headers(extra={"ETag": 'W/"deadbeef"'})
        )
        first = client.get(REPO_PATH, per_page=1)

    assert first.etag == 'W/"deadbeef"'
    assert ledger.get_etag(db.connect(), first.endpoint_key) is not None

    with respx.mock() as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}").respond(
            304, headers=_headers(remaining=4_999)
        )
        second = client.get(REPO_PATH, per_page=1)

    assert route.calls[0].request.headers["If-None-Match"] == 'W/"deadbeef"'
    assert second.status == 304
    assert second.not_modified is True
    assert second.body == body
    # Free against GitHub's quota, still one local call (R-14.7).
    assert client.calls_used == 2


def test_stored_etag_body_is_json_round_tripped(client: GitHubClient, db: Database) -> None:
    body = [{"sha": "a" * 40, "commit": {"committer": {"date": "2019-02-03T00:00:00Z"}}}]
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{COMMITS_PATH}").respond(
            200, json=body, headers=_headers(extra={"ETag": '"xyz"'})
        )
        result = client.get(COMMITS_PATH, per_page=1)

    stored = ledger.get_etag(db.connect(), result.endpoint_key)
    assert stored is not None
    assert json.loads(stored[1]) == body


# -- the single retry (R-14.5, R-14.6) -------------------------------------
def test_one_retry_on_5xx_then_success(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").mock(
            side_effect=[
                httpx.Response(502, json={"message": "Bad gateway"}, headers=_headers()),
                httpx.Response(200, json={"ok": True}, headers=_headers()),
            ]
        )
        result = client.get(REPO_PATH, per_page=1)

    assert result.body == {"ok": True}
    assert result.attempts == 2
    # The retry is charged to the budget (E2).
    assert client.calls_used == 2


def test_at_most_one_retry_on_5xx(client: GitHubClient, db: Database) -> None:
    with respx.mock() as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}").respond(
            500, json={"message": "boom"}, headers=_headers()
        )
        with pytest.raises(UpstreamError) as caught:
            client.get(REPO_PATH, per_page=1)

    assert route.call_count == 2
    assert client.calls_used == 2
    assert caught.value.code is ErrorCode.UPSTREAM_ERROR
    assert caught.value.retryable is True
    assert ledger.count_observations(db.connect()) == 2


def test_one_retry_on_connection_error(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").mock(
            side_effect=[
                httpx.ConnectError("connection refused"),
                httpx.Response(200, json={"ok": True}, headers=_headers()),
            ]
        )
        result = client.get(REPO_PATH, per_page=1)

    assert result.attempts == 2
    assert client.calls_used == 2


def test_two_connection_errors_become_upstream_error(client: GitHubClient) -> None:
    with respx.mock() as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}").mock(
            side_effect=httpx.ConnectTimeout("too slow")
        )
        with pytest.raises(UpstreamError):
            client.get(REPO_PATH, per_page=1)

    assert route.call_count == 2
    assert client.calls_used == 2


def test_retry_after_is_honored_once_and_charged(
    client: GitHubClient, sleeper: RecordingSleep
) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{COMMITS_PATH}").mock(
            side_effect=[
                httpx.Response(
                    403,
                    json={"message": "You have exceeded a secondary rate limit."},
                    headers=_headers(remaining=12, extra={"Retry-After": "1"}),
                ),
                httpx.Response(200, json=[{"sha": "abc"}], headers=_headers(remaining=11)),
            ]
        )
        result = client.get(COMMITS_PATH, per_page=1)

    assert sleeper.waits == [1.0]
    assert result.attempts == 2
    assert client.calls_used == 2


def test_retry_after_is_honored_at_most_once(client: GitHubClient, sleeper: RecordingSleep) -> None:
    with respx.mock() as mock:
        route = mock.get(f"https://api.github.com{COMMITS_PATH}").respond(
            403,
            json={"message": "You have exceeded a secondary rate limit."},
            headers=_headers(remaining=9, extra={"Retry-After": "2"}),
        )
        with pytest.raises(RateLimited) as caught:
            client.get(COMMITS_PATH, per_page=1)

    assert route.call_count == 2
    assert sleeper.waits == [2.0]
    assert client.calls_used == 2
    assert caught.value.details["retry_after_seconds"] == 2


def test_long_retry_after_is_refused_rather_than_slept(
    client: GitHubClient, sleeper: RecordingSleep
) -> None:
    """A 30-minute wait cannot be honored inside a 25 s analysis budget."""
    with respx.mock() as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}").respond(
            403,
            json={"message": "API rate limit exceeded."},
            headers=_headers(remaining=0, extra={"Retry-After": "1800"}),
        )
        with pytest.raises(RateLimited) as caught:
            client.get(REPO_PATH, per_page=1)

    assert route.call_count == 1
    assert sleeper.waits == []
    assert client.calls_used == 1
    assert caught.value.details["retry_after_seconds"] == 1800


def test_retry_is_not_attempted_when_the_budget_is_spent(db: Database) -> None:
    sleeper = RecordingSleep()
    with respx.mock() as mock, GitHubClient(db, sleep=sleeper, budget=1) as c:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(
            500, json={"message": "boom"}, headers=_headers()
        )
        with pytest.raises(CallBudgetExceeded):
            c.get(REPO_PATH, per_page=1)

    assert c.calls_used == 1


# -- status mapping (design.md section 6) ----------------------------------
def test_403_rate_limit_maps_to_rate_limited(client: GitHubClient) -> None:
    with respx.mock() as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}").respond(
            403,
            json={"message": "API rate limit exceeded for 203.0.113.1.", "documentation_url": "x"},
            headers=_headers(remaining=0, used=60, limit=60, reset=2_000_000_000),
        )
        with pytest.raises(RateLimited) as caught:
            client.get(REPO_PATH, per_page=1)

    # No Retry-After and no quota left: retrying would only waste budget.
    assert route.call_count == 1
    assert client.calls_used == 1
    error = caught.value
    assert error.code is ErrorCode.RATE_LIMITED
    assert error.retryable is True
    assert error.http_status == 429
    assert error.details["rate_limit_remaining"] == 0
    assert error.details["reset_at"] is not None
    assert error.details["calls_spent"] == 1


def test_429_maps_to_rate_limited(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(429, json={"message": "slow down"})
        with pytest.raises(RateLimited):
            client.get(REPO_PATH, per_page=1)


def test_plain_403_maps_to_repo_inaccessible(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(
            403, json={"message": "Repository access blocked"}, headers=_headers(remaining=4_000)
        )
        with pytest.raises(RepoInaccessible) as caught:
            client.get(REPO_PATH, per_page=1)

    assert caught.value.code is ErrorCode.REPO_INACCESSIBLE


def test_404_raises_repo_not_found_by_default(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(
            404, json={"message": "Not Found"}, headers=_headers()
        )
        with pytest.raises(RepoNotFound) as caught:
            client.get(REPO_PATH, per_page=1)

    assert caught.value.code is ErrorCode.REPO_NOT_FOUND
    assert client.calls_used == 1


def test_404_is_tolerated_when_asked(client: GitHubClient) -> None:
    """README and releases/latest are expected to 404 (design.md section 4.1)."""
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}/readme").respond(
            404, json={"message": "Not Found"}, headers=_headers()
        )
        result = client.get(f"{REPO_PATH}/readme", per_page=1, tolerate_404=True)

    assert result.status == 404
    assert result.body is None
    assert result.ok is False


def test_409_maps_to_repo_empty(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{COMMITS_PATH}").respond(
            409, json={"message": "Git Repository is empty."}, headers=_headers()
        )
        with pytest.raises(RepoEmpty) as caught:
            client.get(COMMITS_PATH, per_page=1)

    assert caught.value.code is ErrorCode.REPO_EMPTY


def test_non_json_body_becomes_upstream_error(client: GitHubClient) -> None:
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}").respond(
            200, text="<html>maintenance</html>", headers=_headers()
        )
        with pytest.raises(UpstreamError):
            client.get(REPO_PATH, per_page=1)


# -- one page, one read (E2) ----------------------------------------------
def test_per_page_is_always_sent(client: GitHubClient) -> None:
    with respx.mock() as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}/issues").respond(
            200, json=[], headers=_headers()
        )
        client.get(
            f"{REPO_PATH}/issues",
            per_page=30,
            params={"state": "open", "sort": "updated"},
        )

    url = route.calls[0].request.url
    assert url.params["per_page"] == "30"
    assert url.params["state"] == "open"


def test_per_page_out_of_range_is_rejected_before_any_call(client: GitHubClient) -> None:
    with pytest.raises(ValueError, match="per_page"):
        client.get(REPO_PATH, per_page=0)
    with pytest.raises(ValueError, match="per_page"):
        client.get(REPO_PATH, per_page=101)
    assert client.calls_used == 0


def test_next_page_is_reported_never_followed(client: GitHubClient) -> None:
    link = f'<https://api.github.com{REPO_PATH}/issues?page=2>; rel="next"'
    with respx.mock() as mock:
        route = mock.get(f"https://api.github.com{REPO_PATH}/issues").respond(
            200, json=[{"number": 1}], headers=_headers(extra={"Link": link})
        )
        result = client.get(f"{REPO_PATH}/issues", per_page=30)

    assert result.has_next_page is True
    assert route.call_count == 1
    assert client.calls_used == 1


# -- E4: no archives ------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        f"{REPO_PATH}/tarball/main",
        f"{REPO_PATH}/zipball/refs/heads/main",
        f"{REPO_PATH}/archive/main.zip",
        "/codeload/octocat/hello-world/legacy.tar.gz",
    ],
)
def test_archive_paths_are_refused_with_zero_calls(client: GitHubClient, path: str) -> None:
    with pytest.raises(ValueError, match="archives"):
        client.get(path, per_page=1)
    assert client.calls_used == 0


def test_tree_path_is_allowed(client: GitHubClient) -> None:
    """The tree call is what replaces cloning; it must not trip the archive guard."""
    with respx.mock() as mock:
        mock.get(f"https://api.github.com{REPO_PATH}/git/trees/{'a' * 40}").respond(
            200, json={"tree": [], "truncated": False}, headers=_headers()
        )
        result = client.get(
            f"{REPO_PATH}/git/trees/{'a' * 40}", per_page=100, params={"recursive": 1}
        )

    assert result.body == {"tree": [], "truncated": False}
