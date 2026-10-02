import json

import httpx
import pytest

from lazyissues.github import GitHubError, GraphQLGateway
from lazyissues.models import Issue


def node(number: int) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/o/r/issues/{number}",
        "repository": {"nameWithOwner": "o/r"},
        "assignees": {"nodes": [{"login": "me"}]},
        "labels": {"nodes": [{"name": "bug"}]},
        "state": "OPEN",
        "projectItems": {"nodes": []},
    }


def gateway(handler) -> GraphQLGateway:
    return GraphQLGateway("token", transport=httpx.MockTransport(handler))


def test_refuses_to_reach_github_under_pytest():
    with pytest.raises(RuntimeError):
        GraphQLGateway("token")


async def test_search_follows_pages_and_builds_issues():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body["variables"])
        assert request.headers["Authorization"] == "bearer token"
        last = body["variables"]["after"] == "c1"
        page = {
            "pageInfo": {"hasNextPage": not last, "endCursor": None if last else "c1"},
            "nodes": [node(2 if last else 1), {}],  # {} is a non-issue result
        }
        return httpx.Response(200, json={"data": {"search": page}})

    issues = await gateway(handler).search_issues("is:open repo:o/r")

    assert issues == [
        Issue("o/r", n, f"Issue {n}", f"https://github.com/o/r/issues/{n}", ("me",), ("bug",))
        for n in (1, 2)
    ]
    assert [v["after"] for v in sent] == [None, "c1"]
    assert sent[0]["q"] == "is:open repo:o/r"


async def test_search_reads_state_and_each_projects_status():
    def item(owner: str, number: int, status: dict | None) -> dict:
        return {
            "project": {"owner": {"login": owner}, "number": number},
            "fieldValueByName": status,
        }

    closed = node(3) | {
        "state": "CLOSED",
        "projectItems": {
            "nodes": [
                item("o", 1, {"name": "In Progress"}),
                item("acme", 4, None),  # on the project, Status unset
                item("acme", 5, {}),  # Status is not a single-select field
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert 'fieldValueByName(name: "Status")' in json.loads(request.content)["query"]
        page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [closed]}
        return httpx.Response(200, json={"data": {"search": page}})

    [issue] = await gateway(handler).search_issues("x")

    assert issue.closed
    assert issue.project_statuses == {"o/1": "In Progress"}


async def test_graphql_errors_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "bad query"}]})

    with pytest.raises(GitHubError, match="bad query"):
        await gateway(handler).search_issues("x")


async def test_rejected_token_raises_with_fix():
    with pytest.raises(GitHubError, match="gh auth login"):
        await gateway(lambda request: httpx.Response(401)).search_issues("x")


def offline(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("offline")


@pytest.mark.parametrize(
    ("handler", "message"),
    [(lambda request: httpx.Response(502), "HTTP 502"), (offline, "Couldn't reach")],
)
async def test_transport_failures_raise_github_error(handler, message):
    with pytest.raises(GitHubError, match=message):
        await gateway(handler).search_issues("x")
