"""An in-memory GitHub, used by the test suite and by `--demo`."""

import shlex
from dataclasses import dataclass, field, replace

from lazyissues.github import GitHubError
from lazyissues.models import Issue, IssueDetail, Project


@dataclass
class FakeGitHub:
    viewer: str
    issues: list[Issue] = field(default_factory=list)
    closed: set[str] = field(default_factory=set)  # issue keys
    # Body, hierarchy, project fields and activity by issue key. Each detail's `issue` is
    # ignored: it always comes from `issues` and `closed`.
    details: dict[str, IssueDetail] = field(default_factory=dict)
    scopes: set[str] = field(default_factory=lambda: {"repo", "read:org", "project"})
    labels: dict[str, list[str]] = field(default_factory=dict)  # by repo
    projects: dict[str, list[Project]] = field(default_factory=dict)  # linked projects, by repo

    def __post_init__(self) -> None:
        # `closed` is the fake's only record of state; results carry it as `Issue.closed`.
        self.closed |= {issue.key for issue in self.issues if issue.closed}

    async def search_issues(self, query: str) -> list[Issue]:
        return [self._current(issue) for issue in self.issues if self._matches(issue, query)]

    async def issue_detail(self, repo: str, number: int) -> IssueDetail:
        issue = next((i for i in self.issues if (i.repo, i.number) == (repo, number)), None)
        if issue is None:
            raise GitHubError(f"Could not resolve to an Issue with the number of {number}.")
        return replace(self.details.get(issue.key, IssueDetail(issue)), issue=self._current(issue))

    def _current(self, issue: Issue) -> Issue:
        return replace(issue, closed=issue.key in self.closed)

    def _matches(self, issue: Issue, query: str) -> bool:
        """Understands the subset of GitHub search syntax the app sends."""
        repos: list[str] = []
        for term in shlex.split(query):
            qualifier, _, value = term.partition(":")
            match qualifier:
                case "is" if value == "issue":
                    pass
                case "is" | "state":
                    if (issue.key in self.closed) != (value == "closed"):
                        return False
                case "repo":
                    repos.append(value)
                # The fake knows no authors or commenters, so involvement is assignment.
                case "assignee" | "involves":
                    if (self.viewer if value == "@me" else value) not in issue.assignees:
                        return False
                case "no" if value == "assignee":
                    if issue.assignees:
                        return False
                case "label":
                    if value not in issue.labels:
                        return False
                case _:
                    if term.lower() not in issue.title.lower():
                        return False
        return not repos or issue.repo in repos

    async def whoami(self) -> tuple[str, set[str]]:
        return self.viewer, self.scopes

    async def repo_labels(self, repo: str) -> list[str]:
        return self.labels.get(self._resolve(repo), [])

    async def repo_projects(self, repo: str) -> list[Project]:
        return self.projects.get(self._resolve(repo), [])

    def _resolve(self, repo: str) -> str:
        """The repo's own name: GitHub matches names ignoring case."""
        known = {*self.labels, *self.projects, *(issue.repo for issue in self.issues)}
        for name in known:
            if name.casefold() == repo.casefold():
                return name
        raise GitHubError(f"Could not resolve to a Repository with the name '{repo}'.")
