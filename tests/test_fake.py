from dataclasses import replace

import pytest

from lazyissues import demo
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import Issue, IssueDetail, Project


async def test_fake_understands_the_queries_the_app_sends():
    github = demo.github()
    github.closed.add("octo-dev/tidepool#15")

    async def numbers(query: str) -> list[int]:
        return [issue.number for issue in await github.search_issues(query)]

    assert await numbers("is:issue is:open assignee:@me repo:octo-dev/tidepool") == [12]
    assert await numbers("is:closed") == [15]
    assert await numbers("no:assignee") == [18]
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
