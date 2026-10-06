import httpx
import pytest

from lazyissues.github import GitHubError, GraphQLGateway

EMPTY_SEARCH = {"search": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}
RESET = "1767225900"  # 2026-01-01T00:05:00Z
WAIT = {"Retry-After": "7"}


def limited(status: int, **headers: str) -> httpx.Response:
    return httpx.Response(status, headers=headers)


def ok(data: dict = EMPTY_SEARCH) -> httpx.Response:
    return httpx.Response(200, json={"data": data})


def serving(*responses: httpx.Response):
    """A gateway answering with `responses` in turn, the requests it got, and its sleeps."""
    sent: list[httpx.Request] = []
    slept: list[float] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return queue.pop(0)

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    gateway = GraphQLGateway("token", transport=httpx.MockTransport(handler), sleep=sleep)
    return gateway, sent, slept


@pytest.mark.parametrize("status", [403, 429])
async def test_retry_after_says_how_long_to_wait(status):
    gateway, _, _ = serving(limited(status, **WAIT), limited(status, **WAIT))

    with pytest.raises(GitHubError, match=r"rate limit.*try again in 7 seconds"):
        await gateway.search_issues("x")


@pytest.mark.parametrize("status", [403, 429])
async def test_the_reset_header_says_when_the_limit_lifts(status):
    gateway, sent, _ = serving(
        limited(status, **{"x-ratelimit-remaining": "0", "x-ratelimit-reset": RESET})
    )

    with pytest.raises(GitHubError, match=r"rate limit.*try again at 00:05 UTC"):
        await gateway.search_issues("x")
    assert len(sent) == 1  # no Retry-After, so no retry


async def test_a_graphql_rate_limited_error_is_explained_too():
    body = {"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]}
    gateway, _, _ = serving(httpx.Response(200, json=body, headers={"x-ratelimit-reset": RESET}))

    with pytest.raises(GitHubError, match=r"rate limit.*00:05 UTC"):
        await gateway.search_issues("x")


async def test_a_plain_403_is_not_called_a_rate_limit():
    gateway, _, _ = serving(limited(403))

    with pytest.raises(GitHubError, match="HTTP 403"):
        await gateway.search_issues("x")


async def test_a_search_retries_exactly_once_after_retry_after():
    gateway, sent, slept = serving(limited(429, **WAIT), ok())

    assert await gateway.search_issues("x") == []
    assert len(sent) == 2
    assert slept == [7]


async def test_a_search_gives_up_after_one_retry():
    gateway, sent, slept = serving(limited(429, **WAIT), limited(429, **WAIT), ok())

    with pytest.raises(GitHubError, match="rate limit"):
        await gateway.search_issues("x")
    assert len(sent) == 2
    assert slept == [7]


async def test_a_mutation_never_retries():
    lookup = ok({"repository": {"id": "R_1", "issue": {"id": "I_5"}}})
    gateway, sent, slept = serving(lookup, limited(429, **WAIT), ok())

    with pytest.raises(GitHubError, match="rate limit"):
        await gateway.reopen_issue("o/r", 5)
    assert len(sent) == 2  # the id lookup, then the mutation once
    assert slept == []


async def test_a_wait_of_exactly_ten_seconds_is_retried():
    gateway, sent, slept = serving(limited(429, **{"Retry-After": "10"}), ok())

    assert await gateway.search_issues("x") == []
    assert len(sent) == 2
    assert slept == [10]


async def test_a_wait_of_eleven_seconds_raises_at_once():
    gateway, sent, slept = serving(limited(429, **{"Retry-After": "11"}), ok())

    with pytest.raises(GitHubError, match="try again in 11 seconds"):
        await gateway.search_issues("x")
    assert len(sent) == 1
    assert slept == []
