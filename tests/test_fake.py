from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lazyissues import demo
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import CloseReason, Issue, IssueDetail, Milestone, Project


async def test_fake_understands_the_queries_the_app_sends():
    github = demo.github()
    github.closed.add("octo-dev/tidepool#15")

    async def numbers(query: str) -> list[int]:
        return [issue.number for issue in await github.search_issues(query)]

    assert await numbers("is:issue is:open assignee:@me repo:octo-dev/tidepool") == [12]
    assert await numbers("is:closed") == [15, 10, 2]
    assert await numbers("no:assignee") == [18, 13]
    assert await numbers("assignee:sam-reef repo:octo-dev/lanternfish") == [4, 7]
    assert await numbers('label:bug "tide table"') == [12]
    # The fake knows no authors or commenters, so involvement is assignment.
    assert await numbers("is:issue is:open involves:@me") == [12, 4, 9, 11]


async def test_fake_returns_state_and_project_statuses_like_github():
    github = demo.github()
    github.closed.add("octo-dev/tidepool#15")
    found = {issue.key: issue for issue in await github.search_issues("is:issue")}
    assert found["octo-dev/tidepool#15"].closed
    assert not found["octo-dev/tidepool#12"].closed
    assert found["octo-dev/lanternfish#4"].project_statuses == {"octo-dev/3": "In Progress"}


async def test_fake_treats_an_issue_seeded_closed_as_closed():
    github = FakeGitHub(viewer="me", issues=[Issue("a/x", 1, "Old", "u", closed=True)])
    assert [issue.closed for issue in await github.search_issues("is:closed")] == [True]
    assert await github.search_issues("is:open") == []


async def test_fake_detail_reflects_the_current_issue_and_state():
    github = demo.github()
    key = "octo-dev/tidepool#12"
    github.details[key] = IssueDetail(github.issues[0], body="Old")
    github.issues[0] = replace(github.issues[0], title="Renamed")
    github.closed.add(key)

    detail = await github.issue_detail("octo-dev/tidepool", 12)

    assert (detail.issue.title, detail.issue.closed, detail.body) == ("Renamed", True, "Old")


async def test_fake_detail_of_an_issue_with_no_extras_is_empty():
    detail = await FakeGitHub(viewer="me", issues=[Issue("o/r", 1, "One", "u")]).issue_detail(
        "o/r", 1
    )
    assert detail == IssueDetail(Issue("o/r", 1, "One", "u"))


async def test_fake_detail_of_a_missing_issue_raises():
    with pytest.raises(GitHubError, match="Could not resolve"):
        await demo.github().issue_detail("octo-dev/tidepool", 999)


async def test_fake_answers_viewer_and_repo_questions():
    project = Project("o/1", "Board", ("Todo", "Done"))
    github = FakeGitHub(viewer="me", labels={"o/r": ["bug"]}, projects={"o/r": [project]})
    login, scopes = await github.whoami()
    assert login == "me"
    assert "project" in scopes
    assert await github.repo_labels("o/r") == ["bug"]
    assert await github.repo_projects("o/r") == [project]


async def test_fake_rejects_unknown_repos_like_github():
    with pytest.raises(GitHubError, match="Could not resolve"):
        await FakeGitHub(viewer="me").repo_labels("o/missing")


async def test_fake_matches_repo_names_ignoring_case_like_github():
    github = FakeGitHub(viewer="me", labels={"o/r": ["bug"]})
    assert await github.repo_labels("O/R") == ["bug"]


async def test_fake_finds_issues_closed_on_or_after_a_date_like_github():
    def closed(number: int, at: str | None) -> Issue:
        return Issue("a/x", number, "Old", "u", closed=True, closed_at=at)

    github = FakeGitHub(
        viewer="me",
        issues=[
            closed(1, "2026-09-20T23:59:59Z"),
            closed(2, "2026-09-21T00:00:00Z"),
            Issue("a/x", 3, "Open", "u"),
        ],
    )
    found = await github.search_issues("is:closed closed:>=2026-09-21")
    assert [issue.number for issue in found] == [2]
    assert found[0].closed_at == "2026-09-21T00:00:00Z"


async def test_fake_counts_an_issue_closed_during_the_session_as_closed_today():
    github = FakeGitHub(viewer="me", issues=[Issue("a/x", 1, "Open", "u")])
    github.closed.add("a/x#1")
    today = datetime.now(UTC).date().isoformat()
    assert len(await github.search_issues(f"closed:>={today}")) == 1


def writable() -> FakeGitHub:
    return FakeGitHub(
        viewer="me",
        issues=[
            Issue("o/r", 1, "One", "u", labels=("bug", "todo")),
            Issue("o/r", 2, "Two", "u", labels=("in-progress",)),
            Issue("o/b", 3, "Three", "u", project_statuses={"o/1": "Todo"}),
            Issue("o/b", 4, "Four", "u"),
        ],
        labels={"o/r": ["bug", "todo", "in-progress"]},
        projects={"o/b": [Project("o/1", "Board", ("Todo", "In progress", "Done"))]},
    )


async def found(github: FakeGitHub, number: int) -> Issue:
    [issue] = [i for i in await github.search_issues("is:issue") if i.number == number]
    return issue


async def test_fake_adds_the_repos_label_for_a_status_or_creates_it():
    github = writable()
    await github.add_label("o/r", 1, "In Progress")  # the repo has `in-progress`
    await github.add_label("o/r", 1, "In Review")  # the repo has no such label
    await github.remove_labels("o/r", 1, ["todo"])

    assert (await found(github, 1)).labels == ("bug", "in-progress", "In Review")
    assert github.labels["o/r"] == ["bug", "todo", "in-progress", "In Review"]


async def test_fake_adds_issues_to_projects_and_sets_their_status():
    github = writable()
    assert await github.project_status_options("o/1") == ["Todo", "In progress", "Done"]

    await github.set_project_status("o/b", 3, "o/1", "In Progress")
    assert (await found(github, 3)).project_statuses == {"o/1": "In progress"}

    with pytest.raises(GitHubError, match="not on the project"):
        await github.set_project_status("o/b", 4, "o/1", "Todo")
    await github.add_to_project("o/b", 4, "o/1")
    await github.set_project_status("o/b", 4, "o/1", "Todo")
    assert (await found(github, 4)).project_statuses == {"o/1": "Todo"}

    with pytest.raises(GitHubError, match="no option"):
        await github.set_project_status("o/b", 4, "o/1", "Blocked")


async def test_fake_closes_with_a_reason_and_reopens():
    github = writable()
    await github.close_issue("o/r", 1, CloseReason.DUPLICATE, "o/r#2")
    assert (await found(github, 1)).closed
    assert github.close_reasons["o/r#1"] == (CloseReason.DUPLICATE, "o/r#2")

    with pytest.raises(GitHubError, match="Could not resolve"):
        await github.close_issue("o/r", 2, CloseReason.DUPLICATE, "o/r#99")

    await github.reopen_issue("o/r", 1)
    assert not (await found(github, 1)).closed


async def test_fake_assigns():
    github = writable()
    await github.assign("o/r", 2, "me")
    assert (await found(github, 2)).assignees == ("me",)


async def test_fake_rejects_writes_to_a_read_only_repo():
    github = writable()
    github.read_only.add("o/r")
    with pytest.raises(GitHubError, match="Resource not accessible"):
        await github.add_label("o/r", 1, "Todo")
    assert (await found(github, 1)).labels == ("bug", "todo")


def in_milestone(number: int, repo: str, milestone: str, closed: bool = False) -> Issue:
    return Issue(repo, number, "One", "u", closed=closed, milestone=milestone)


MILESTONED = FakeGitHub(
    viewer="me",
    issues=[
        in_milestone(1, "a/x", "Big launch"),
        in_milestone(2, "a/x", "Big launch", closed=True),
        in_milestone(3, "a/x", "Big launch", closed=True),
        in_milestone(4, "a/y", "Big launch"),
        in_milestone(5, "a/x", "Later"),
    ],
    milestones={"a/x": ["Big launch", "Empty"], "a/y": ["Big launch"]},
)


async def test_fake_lists_a_repos_milestones_counting_their_open_and_closed_issues():
    assert await MILESTONED.repo_milestones("a/x") == [
        Milestone("a/x", "Big launch", open=1, closed=2),
        Milestone("a/x", "Empty"),
    ]


async def test_fake_finds_a_milestones_issues_in_one_repo():
    found = await MILESTONED.search_issues('is:open repo:a/x milestone:"Big launch"')
    assert [issue.key for issue in found] == ["a/x#1"]


async def test_fake_reports_a_query_it_cannot_parse_as_a_github_error():
    with pytest.raises(GitHubError, match="quotation"):
        await demo.github().search_issues('label:"needs triage')
