"""The GraphQL shape of the gateway's writes, against a recorded-style GitHub."""

import json
import re
from typing import Any

import httpx
import pytest

from lazyissues.github import GitHubError, GraphQLGateway
from lazyissues.models import CloseReason

REPO_LABELS = [
    {"id": "L1", "name": "bug"},
    {"id": "L2", "name": "in-progress"},
    {"id": "L3", "name": "todo"},
]


def repository(issue_id: str = "I1") -> dict[str, Any]:
    return {
        "id": "R1",
        "issue": {
            "id": issue_id,
            "labels": {"nodes": [{"id": "L1", "name": "bug"}, {"id": "L3", "name": "todo"}]},
            "projectItems": {"nodes": [{"id": "PVTI1", "project": {"id": "P1"}}]},
        },
    }


STATUS_FIELD = {
    "id": "F1",
    "options": [{"id": "o1", "name": "Todo"}, {"id": "o2", "name": "In progress"}],
}


class GitHub:
    """Answers the gateway's lookups from canned data and records its mutations."""

    def __init__(self) -> None:
        self.repositories = {("o", "r", 1): repository(), ("o", "r2", 5): repository("I5")}
        self.project: dict[str, Any] | None = {"id": "P1", "field": STATUS_FIELD}
        self.users = {"sam": {"id": "U1"}}
        self.lookups: list[dict[str, Any]] = []
        self.mutations: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        query, variables = body["query"], body["variables"]
        if mutation := re.match(r"\s*mutation\(\$input: (\w+)Input!\) \{ (\w+)\(", query):
            kind, name = mutation.groups()
            assert kind == name[0].upper() + name[1:]
            self.mutations.append((name, variables["input"]))
            payload = {"label": {"id": "L-new"}} if name == "createLabel" else {}
            return httpx.Response(200, json={"data": {name: payload}})
        self.lookups.append(variables)
        if "repositoryOwner" in query:
            data = {"repositoryOwner": {"projectV2": self.project}}
        elif "labels(first: 100, after" in query:  # the repo's labels, paged
            page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": REPO_LABELS}
            data = {"repository": {"labels": page}}
        elif "user(login" in query:
            data = {"user": self.users.get(variables["login"])}
        else:
            key = (variables["owner"], variables["name"], variables["number"])
            data = {"repository": self.repositories[key]}
        return httpx.Response(200, json={"data": data})


@pytest.fixture
def github() -> GitHub:
    return GitHub()


@pytest.fixture
def gateway(github: GitHub) -> GraphQLGateway:
    return GraphQLGateway("token", transport=httpx.MockTransport(github))


async def test_add_label_uses_the_repos_label_spelled_any_way(gateway, github):
    await gateway.add_label("o/r", 1, "In Progress")

    assert github.lookups == [
        {"owner": "o", "name": "r", "number": 1},
        {"owner": "o", "name": "r", "after": None},
    ]
    assert github.mutations == [("addLabelsToLabelable", {"labelableId": "I1", "labelIds": ["L2"]})]


async def test_add_label_creates_a_label_the_repo_lacks(gateway, github):
    await gateway.add_label("o/r", 1, "In Review")

    assert github.mutations == [
        ("createLabel", {"repositoryId": "R1", "name": "In Review", "color": "ededed"}),
        ("addLabelsToLabelable", {"labelableId": "I1", "labelIds": ["L-new"]}),
    ]


async def test_remove_labels_sends_the_issues_label_ids(gateway, github):
    await gateway.remove_labels("o/r", 1, ["todo", "not-on-the-issue"])

    assert github.mutations == [
        ("removeLabelsFromLabelable", {"labelableId": "I1", "labelIds": ["L3"]})
    ]


async def test_project_status_options_in_board_order(gateway, github):
    assert await gateway.project_status_options("o/1") == ["Todo", "In progress"]
    assert github.lookups == [{"login": "o", "number": 1}]


async def test_a_project_without_a_single_select_status_raises(gateway, github):
    github.project = {"id": "P1", "field": {}}  # a Status field of another type
    with pytest.raises(GitHubError, match="no single-select Status field"):
        await gateway.project_status_options("o/1")


async def test_add_to_project(gateway, github):
    await gateway.add_to_project("o/r", 1, "o/1")

    assert github.mutations == [("addProjectV2ItemById", {"projectId": "P1", "contentId": "I1"})]


async def test_set_project_status_finds_the_item_field_and_option_by_name(gateway, github):
    await gateway.set_project_status("o/r", 1, "o/1", "In Progress")

    assert github.mutations == [
        (
            "updateProjectV2ItemFieldValue",
            {
                "projectId": "P1",
                "itemId": "PVTI1",
                "fieldId": "F1",
                "value": {"singleSelectOptionId": "o2"},
            },
        )
    ]


async def test_set_project_status_needs_the_issue_on_the_project_and_the_option(gateway, github):
    with pytest.raises(GitHubError, match="has no Status Blocked"):
        await gateway.set_project_status("o/r", 1, "o/1", "Blocked")

    github.repositories["o", "r", 1]["issue"]["projectItems"]["nodes"] = []
    with pytest.raises(GitHubError, match="isn't on project o/1"):
        await gateway.set_project_status("o/r", 1, "o/1", "Todo")
    assert github.mutations == []


async def test_close_with_a_reason(gateway, github):
    await gateway.close_issue("o/r", 1, CloseReason.NOT_PLANNED)

    assert github.mutations == [("closeIssue", {"issueId": "I1", "stateReason": "NOT_PLANNED"})]


async def test_close_as_a_duplicate_links_the_original(gateway, github):
    await gateway.close_issue("o/r", 1, CloseReason.DUPLICATE, "o/r2#5")

    assert github.mutations == [
        ("closeIssue", {"issueId": "I1", "stateReason": "DUPLICATE", "duplicateIssueId": "I5"})
    ]


async def test_reopen(gateway, github):
    await gateway.reopen_issue("o/r", 1)

    assert github.mutations == [("reopenIssue", {"issueId": "I1"})]


async def test_assign_looks_up_the_user(gateway, github):
    await gateway.assign("o/r", 1, "sam")

    assert github.mutations == [
        ("addAssigneesToAssignable", {"assignableId": "I1", "assigneeIds": ["U1"]})
    ]
    with pytest.raises(GitHubError, match="No GitHub user nobody"):
        await gateway.assign("o/r", 1, "nobody")
