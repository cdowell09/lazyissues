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
            if name == "addAssigneesToAssignable":
                payload = {"assignable": issue_node(1)}
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


async def test_add_labels_uses_the_repos_label_spelled_any_way(gateway, github):
    await gateway.add_labels("o/r", 1, ["In Progress"])

    assert github.lookups == [
        {"owner": "o", "name": "r", "number": 1},
        {"owner": "o", "name": "r", "after": None},
    ]
    assert github.mutations == [("addLabelsToLabelable", {"labelableId": "I1", "labelIds": ["L2"]})]


async def test_add_labels_creates_a_label_the_repo_lacks(gateway, github):
    await gateway.add_labels("o/r", 1, ["In Review"])

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


def requests(github: GitHub) -> int:
    return len(github.lookups) + len(github.mutations)


async def test_a_label_move_sends_four_requests_and_a_repeat_three(gateway, github):
    await gateway.add_labels("o/r", 1, ["In Progress"])
    await gateway.remove_labels("o/r", 1, ["todo"])
    assert requests(github) == 4  # issue ID, labels, add, remove

    github.repositories[("o", "r", 2)] = repository("I2")
    before = requests(github)
    await gateway.add_labels("o/r", 2, ["In Progress"])
    await gateway.remove_labels("o/r", 2, ["todo"])
    assert requests(github) - before == 3  # issue ID, add, remove
    assert github.mutations[-2:] == [
        ("addLabelsToLabelable", {"labelableId": "I2", "labelIds": ["L2"]}),
        ("removeLabelsFromLabelable", {"labelableId": "I2", "labelIds": ["L3"]}),
    ]


async def test_a_label_created_by_one_move_serves_the_next_without_a_refetch(gateway, github):
    await gateway.add_labels("o/r", 1, ["In Review"])
    before = requests(github)
    github.repositories[("o", "r", 2)] = repository("I2")
    await gateway.add_labels("o/r", 2, ["In Review"])

    assert github.mutations[-1] == (
        "addLabelsToLabelable",
        {"labelableId": "I2", "labelIds": ["L-new"]},
    )
    assert requests(github) - before == 2  # issue ID and the add: no label fetch, no create


async def test_several_labels_go_in_one_request(gateway, github):
    await gateway.add_labels("o/r", 1, ["bug", "In Progress", "In Review"])

    assert [name for name, _ in github.mutations] == ["createLabel", "addLabelsToLabelable"]
    assert github.mutations[-1][1]["labelIds"] == ["L1", "L2", "L-new"]


async def test_an_issues_id_is_looked_up_once(gateway, github):
    await gateway.reopen_issue("o/r", 1)
    await gateway.reopen_issue("o/r", 1)

    assert len(github.lookups) == 1


async def test_a_user_id_is_looked_up_once(gateway, github):
    await gateway.assign("o/r", 1, "sam")
    await gateway.assign("o/r", 1, "sam")

    assert sum("login" in lookup for lookup in github.lookups) == 1


async def test_a_board_is_fetched_once(gateway, github):
    await gateway.project_status_options("o/1")
    await gateway.add_to_project("o/r", 1, "o/1")

    assert sum("number" in lookup and "login" in lookup for lookup in github.lookups) == 1


def issue_node(number: int) -> dict[str, Any]:
    return {
        "id": f"I_{number}",
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/o/r/issues/{number}",
        "repository": {"nameWithOwner": "o/r"},
        "assignees": {"nodes": [{"login": "sam"}]},
        "labels": {"nodes": []},
        "state": "OPEN",
        "closedAt": None,
        "milestone": None,
        "parent": None,
        "projectItems": {"nodes": []},
    }


async def test_a_bulk_assign_sends_one_lean_request_per_issue():
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        query = body["query"]
        if "user(login" in query:
            return httpx.Response(200, json={"data": {"user": {"id": "U1"}}})
        if query.lstrip().startswith("mutation"):
            number = int(body["variables"]["input"]["assignableId"][2:])
            payload = {"assignable": issue_node(number)}
            return httpx.Response(200, json={"data": {"addAssigneesToAssignable": payload}})
        nodes = [issue_node(n) for n in range(1, 51)]
        page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}
        return httpx.Response(200, json={"data": {"search": page}})

    gateway = GraphQLGateway("token", transport=httpx.MockTransport(handler))
    issues = await gateway.search_issues("is:open")
    sent.clear()

    assigned = [await gateway.assign(i.repo, i.number, "sam") for i in issues]

    mutations = [b for b in sent if b["query"].lstrip().startswith("mutation")]
    assert len(sent) == 51  # 50 mutations and one user lookup
    assert all("DetailFields" not in b["query"] for b in mutations)
    assert [i.number for i in assigned] == list(range(1, 51))
    assert assigned[0].assignees == ("sam",)
