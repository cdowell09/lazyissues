import json
from datetime import UTC, datetime

import httpx
import pytest

from lazyissues.github import GitHubError, GraphQLGateway
from lazyissues.models import Event, Issue, Milestone, Project, ProjectField


def node(number: int) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://github.com/o/r/issues/{number}",
        "repository": {"nameWithOwner": "o/r"},
        "assignees": {"nodes": [{"login": "me"}]},
        "labels": {"nodes": [{"name": "bug"}]},
        "state": "OPEN",
        "closedAt": None,
        "milestone": None,
        "parent": None,
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
        "closedAt": "2026-09-30T12:00:00Z",
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
    assert issue.closed_at == "2026-09-30T12:00:00Z"
    assert issue.project_statuses == {"o/1": "In Progress"}


async def test_search_reads_the_milestone_and_the_parent_issue():
    sub = node(4) | {
        "milestone": {"title": "v1"},
        "parent": {"number": 9, "repository": {"nameWithOwner": "o/other"}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [sub, node(5)]}
        return httpx.Response(200, json={"data": {"search": page}})

    issues = await gateway(handler).search_issues("x")

    assert [(i.milestone, i.parent) for i in issues] == [("v1", "o/other#9"), (None, None)]


def at(minute: int) -> datetime:
    return datetime(2026, 9, 1, 12, minute, tzinfo=UTC)


def stamp(minute: int) -> str:
    return f"2026-09-01T12:{minute:02}:00Z"


async def test_issue_detail_builds_body_hierarchy_project_fields_and_activity():
    sent = []
    issue = node(5) | {
        "body": "Steps:\n\n1. Open",
        "state": "CLOSED",
        "milestone": {"title": "v1"},
        "parent": node(1),
        "subIssues": {"nodes": [node(6), node(7)]},
        "projectItems": {
            "nodes": [
                {
                    # Merged from `IssueFields` (Status) and the detail (every field).
                    "project": {"title": "Roadmap", "number": 2, "owner": {"login": "o"}},
                    "fieldValueByName": {"name": "Todo"},
                    "fieldValues": {
                        "nodes": [
                            {"name": "Reliability", "field": {"name": "Theme"}},
                            {"title": "Sprint 3", "field": {"name": "Iteration"}},
                            {},  # a field type the detail doesn't show
                        ]
                    },
                }
            ]
        },
        "timelineItems": {
            "nodes": [
                {
                    "__typename": "LabeledEvent",
                    "actor": {"login": "me"},
                    "createdAt": stamp(0),
                    "label": {"name": "bug"},
                },
                {
                    "__typename": "IssueComment",
                    "actor": {"login": "sam"},  # aliased from `author`
                    "createdAt": stamp(1),
                    "body": "Seen it **too**",
                },
                {
                    "__typename": "AssignedEvent",
                    "actor": {"login": "me"},
                    "createdAt": stamp(2),
                    "assignee": {"login": "sam"},
                },
                {
                    "__typename": "MilestonedEvent",
                    "actor": None,  # a deleted account
                    "createdAt": stamp(3),
                    "milestoneTitle": "v1",
                },
                {
                    "__typename": "ClosedEvent",
                    "actor": {"login": "me"},
                    "createdAt": stamp(4),
                    "stateReason": "NOT_PLANNED",
                },
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["variables"])
        return httpx.Response(200, json={"data": {"repository": {"issue": issue}}})

    detail = await gateway(handler).issue_detail("o/r", 5)

    assert sent == [{"owner": "o", "name": "r", "number": 5}]
    assert detail.issue.key == "o/r#5"
    assert detail.issue.closed
    assert detail.issue.project_statuses == {"o/2": "Todo"}
    assert detail.body == "Steps:\n\n1. Open"
    assert detail.issue.milestone == "v1"
    assert detail.parent is not None
    assert detail.parent.key == "o/r#1"
    assert [sub.number for sub in detail.sub_issues] == [6, 7]
    assert detail.project_fields == (
        ProjectField("Roadmap", "Theme", "Reliability"),
        ProjectField("Roadmap", "Iteration", "Sprint 3"),
    )
    assert detail.activity == (
        Event("me", at(0), "labeled", "bug"),
        Event("sam", at(1), "commented", "Seen it **too**"),
        Event("me", at(2), "assigned", "sam"),
        Event("ghost", at(3), "milestoned", "v1"),
        Event("me", at(4), "closed", "not planned"),
    )
    assert detail.comments == (Event("sam", at(1), "commented", "Seen it **too**"),)


async def test_issue_detail_of_an_open_issue_without_extras():
    issue = node(5) | {
        "body": "",
        "milestone": None,
        "parent": None,
        "subIssues": {"nodes": []},
        "projectItems": {"nodes": []},
        "timelineItems": {"nodes": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"repository": {"issue": issue}}})

    detail = await gateway(handler).issue_detail("o/r", 5)

    assert (detail.issue.milestone, detail.parent, detail.activity) == (None, None, ())


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


def answer(data: dict, headers: dict | None = None):
    """A handler that returns `data`, and the list of variables it was sent."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["variables"])
        return httpx.Response(200, json={"data": data}, headers=headers)

    return handler, sent


def detail_node(number: int, **extra) -> dict:
    """An issue as the detail fields select it, as mutations return it."""
    return (
        node(number)
        | {
            "body": "",
            "milestone": None,
            "parent": None,
            "subIssues": {"nodes": []},
            "timelineItems": {"nodes": []},
        }
        | extra
    )


def replying(*responses: dict):
    """A handler answering each request with the next response, recording what was sent."""
    sent: list[dict] = []
    answers = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"data": next(answers)})

    return handler, sent


@pytest.mark.parametrize(
    ("headers", "scopes"),
    [
        ({"X-OAuth-Scopes": "repo, read:org, project"}, {"repo", "read:org", "project"}),
        ({"X-OAuth-Scopes": ""}, set()),
        ({}, set()),
    ],
)
async def test_whoami_gives_the_login_and_the_token_scopes_from_the_response_header(
    headers, scopes
):
    handler, _ = answer({"viewer": {"login": "me"}}, headers)
    assert await gateway(handler).whoami() == ("me", scopes)


async def test_repo_labels_follows_pages():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        variables = json.loads(request.content)["variables"]
        sent.append(variables)
        last = variables["after"] == "c1"
        labels = {
            "pageInfo": {"hasNextPage": not last, "endCursor": None if last else "c1"},
            "nodes": [{"name": "x" if last else "bug"}],
        }
        return httpx.Response(200, json={"data": {"repository": {"labels": labels}}})

    assert await gateway(handler).repo_labels("o/r") == ["bug", "x"]
    assert sent == [
        {"owner": "o", "name": "r", "after": None},
        {"owner": "o", "name": "r", "after": "c1"},
    ]


async def test_repo_projects_lists_open_projects_with_status_options_in_board_order():
    def project(number: int, closed: bool = False, status: dict | None = None) -> dict:
        return {
            "number": number,
            "title": f"Board {number}",
            "closed": closed,
            "owner": {"login": "o"},
            "field": status,
        }

    options = {"options": [{"name": "Todo"}, {"name": "In Progress"}, {"name": "Done"}]}
    nodes = [project(1, status=options), project(2, closed=True), project(3)]
    handler, sent = answer({"repository": {"projectsV2": {"nodes": nodes}}})

    assert await gateway(handler).repo_projects("o/r") == [
        Project("o/1", "Board 1", ("Todo", "In Progress", "Done")),
        Project("o/3", "Board 3"),
    ]
    assert sent == [{"owner": "o", "name": "r"}]


async def test_repo_milestones_lists_open_milestones_with_open_and_closed_issue_counts():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body["variables"])
        assert "states: [OPEN]" in body["query"]
        last = body["variables"]["after"] == "c1"
        milestones = {
            "pageInfo": {"hasNextPage": not last, "endCursor": None if last else "c1"},
            "nodes": [
                {
                    "title": "v2" if last else "v1",
                    "open": {"totalCount": 3},
                    "closed": {"totalCount": 0 if last else 5},
                }
            ],
        }
        return httpx.Response(200, json={"data": {"repository": {"milestones": milestones}}})

    assert await gateway(handler).repo_milestones("o/r") == [
        Milestone("o/r", "v1", open=3, closed=5),
        Milestone("o/r", "v2", open=3, closed=0),
    ]
    assert sent == [
        {"owner": "o", "name": "r", "after": None},
        {"owner": "o", "name": "r", "after": "c1"},
    ]


def target(id: str = "I_5") -> dict:
    """The write target lookup's answer: the repo's and the issue's IDs."""
    issue = {"id": id, "labels": {"nodes": []}, "projectItems": {"nodes": []}}
    return {"repository": {"id": "R_1", "issue": issue}}


def mutation(sent: dict) -> tuple[str, dict]:
    """The mutation a request ran, and its input."""
    name = sent["query"].split("{", 2)[1].split("(")[0].strip()
    return name, sent["variables"]["input"]


async def test_comment_adds_it_to_the_issue_and_returns_the_fresh_detail():
    comment = {
        "__typename": "IssueComment",
        "actor": {"login": "me"},
        "createdAt": stamp(0),
        "body": "On it",
    }
    handler, sent = replying(
        target(), {"addComment": {"subject": detail_node(5, timelineItems={"nodes": [comment]})}}
    )

    detail = await gateway(handler).comment("o/r", 5, "On it")

    assert sent[0]["variables"] == {"owner": "o", "name": "r", "number": 5}
    assert mutation(sent[1]) == ("addComment", {"subjectId": "I_5", "body": "On it"})
    assert "subject { ...DetailFields }" in sent[1]["query"]
    assert detail.comments == (Event("me", at(0), "commented", "On it"),)


def assignees_github(fail: str | None = None):
    """Answers the lookups `change_assignees` makes, in whatever order they arrive, and
    records the mutations; `fail` names a mutation GitHub rejects."""
    mutations: list[tuple[str, dict]] = []
    assigned = {"assignees": {"nodes": [{"login": "kim"}, {"login": "sam"}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["query"].lstrip().startswith("mutation"):
            name, fields = mutation(body)
            mutations.append((name, fields))
            if name == fail:
                return httpx.Response(200, json={"errors": [{"message": "Forbidden"}]})
            data = {name: {"assignable": detail_node(5, **assigned)}}
        elif "user(login" in body["query"]:
            data = {"user": {"id": f"U_{body['variables']['login']}"}}
        else:
            data = target()
        return httpx.Response(200, json={"data": data})

    return handler, mutations


async def test_change_assignees_adds_then_removes_by_user_id():
    handler, mutations = assignees_github()

    detail = await gateway(handler).change_assignees("o/r", 5, add=["sam"], remove=["me"])

    assert mutations == [
        ("addAssigneesToAssignable", {"assignableId": "I_5", "assigneeIds": ["U_sam"]}),
        ("removeAssigneesFromAssignable", {"assignableId": "I_5", "assigneeIds": ["U_me"]}),
    ]
    assert detail.issue.assignees == ("kim", "sam")  # from the last mutation's payload


async def test_change_assignees_sends_only_the_side_that_changes():
    handler, mutations = assignees_github()
    await gateway(handler).change_assignees("o/r", 5, add=[], remove=["me"])
    assert [name for name, _ in mutations] == ["removeAssigneesFromAssignable"]


async def test_change_assignees_says_an_add_went_through_when_the_remove_fails():
    handler, _ = assignees_github(fail="removeAssigneesFromAssignable")
    with pytest.raises(GitHubError, match="Added sam, but couldn't remove me: Forbidden"):
        await gateway(handler).change_assignees("o/r", 5, add=["sam"], remove=["me"])


def page(nodes: list[dict]) -> dict:
    return {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}


async def test_assignable_users_lists_logins():
    users = page([{"login": "me"}, {"login": "sam"}])
    handler, sent = replying({"repository": {"assignableUsers": users}})

    assert await gateway(handler).assignable_users("o/r") == ["me", "sam"]
    assert sent[0]["variables"] == {"owner": "o", "name": "r", "after": None}


def labels(*names: str) -> dict:
    return {"repository": {"labels": page([{"id": f"L_{n}", "name": n} for n in names])}}


def milestones(*titles: str) -> dict:
    nodes = [{"id": f"M_{title}", "title": title} for title in titles]
    return {"repository": {"milestones": page(nodes)}}


async def test_create_issue_resolves_labels_and_milestone_by_name():
    created = detail_node(9, body="Steps", milestone={"title": "v1"})
    handler, sent = replying(
        {"repository": {"id": "R_1"}},
        labels("bug", "todo"),
        milestones("v1"),
        {"createIssue": {"issue": created}},
    )

    detail = await gateway(handler).create_issue(
        "o/r", "Issue 9", body="Steps", labels=["todo"], milestone="v1"
    )

    assert mutation(sent[-1]) == (
        "createIssue",
        {
            "repositoryId": "R_1",
            "title": "Issue 9",
            "body": "Steps",
            "labelIds": ["L_todo"],
            "milestoneId": "M_v1",
        },
    )
    assert (detail.issue.number, detail.body, detail.issue.milestone) == (9, "Steps", "v1")


async def test_create_issue_without_labels_or_milestone_looks_none_up():
    handler, sent = replying(
        {"repository": {"id": "R_1"}}, {"createIssue": {"issue": detail_node(9)}}
    )

    await gateway(handler).create_issue("o/r", "Issue 9")

    assert mutation(sent[-1])[1] == {
        "repositoryId": "R_1",
        "title": "Issue 9",
        "body": "",
        "labelIds": [],
        "milestoneId": None,
    }


async def test_an_unknown_label_raises_before_writing():
    handler, sent = replying({"repository": {"id": "R_1"}}, labels("bug"))

    with pytest.raises(GitHubError, match="o/r has no label 'nope'"):
        await gateway(handler).create_issue("o/r", "x", labels=["nope"])
    assert len(sent) == 2


async def test_update_issue_sends_only_the_changed_fields():
    handler, sent = replying(target(), {"updateIssue": {"issue": detail_node(5)}})

    await gateway(handler).update_issue("o/r", 5, {"title": "New", "body": "Text"})

    assert mutation(sent[-1]) == ("updateIssue", {"id": "I_5", "title": "New", "body": "Text"})


async def test_update_issue_clears_the_milestone():
    handler, sent = replying(target(), {"updateIssue": {"issue": detail_node(5)}})

    await gateway(handler).update_issue("o/r", 5, {"milestone": None})

    assert mutation(sent[-1]) == ("updateIssue", {"id": "I_5", "milestoneId": None})
