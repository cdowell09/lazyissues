"""An in-memory GitHub, used by the test suite and by `--demo`."""

import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from lazyissues.github import GitHubError
from lazyissues.models import CloseReason, Issue, IssueDetail, Milestone, Project, parse_key
from lazyissues.statuses import normalize


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
    close_reasons: dict[str, tuple[CloseReason, str | None]] = field(default_factory=dict)
    read_only: set[str] = field(default_factory=set)  # repos that reject every write
    # (project, issue key) for each issue on a project, Status set or not.
    project_items: set[tuple[str, str]] = field(default_factory=set)
    # Open milestones' titles, by repo; their issue counts come from `issues`.
    milestones: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # `closed` is the fake's only record of state; results carry it as `Issue.closed`.
        self.closed |= {issue.key for issue in self.issues if issue.closed}
        self.project_items |= {
            (project, issue.key) for issue in self.issues for project in issue.project_statuses
        }

    async def search_issues(self, query: str) -> list[Issue]:
        return [self._current(issue) for issue in self.issues if self._matches(issue, query)]

    async def issue_detail(self, repo: str, number: int) -> IssueDetail:
        issue = self.issues[self._index(repo, number)]
        return replace(self.details.get(issue.key, IssueDetail(issue)), issue=self._current(issue))

    async def project_status_options(self, project: str) -> list[str]:
        for linked in self.projects.values():
            for board in linked:
                if board.ref == project:
                    return list(board.status_options)
        raise GitHubError(f"Could not resolve to a ProjectV2 with the number {project}.")

    async def add_label(self, repo: str, number: int, name: str) -> None:
        index = self._writable(repo, number)
        repo_labels = self.labels.setdefault(repo, [])
        label = next((lb for lb in repo_labels if normalize(lb) == normalize(name)), None)
        if label is None:
            label = name
            repo_labels.append(label)
        issue = self.issues[index]
        if label not in issue.labels:
            self.issues[index] = replace(issue, labels=(*issue.labels, label))

    async def remove_labels(self, repo: str, number: int, names: Sequence[str]) -> None:
        index = self._writable(repo, number)
        issue = self.issues[index]
        self.issues[index] = replace(
            issue, labels=tuple(label for label in issue.labels if label not in names)
        )

    async def add_to_project(self, repo: str, number: int, project: str) -> None:
        index = self._writable(repo, number)
        await self.project_status_options(project)
        self.project_items.add((project, self.issues[index].key))

    async def set_project_status(self, repo: str, number: int, project: str, status: str) -> None:
        index = self._writable(repo, number)
        issue = self.issues[index]
        options = await self.project_status_options(project)
        if (project, issue.key) not in self.project_items:
            raise GitHubError(f"{issue.key} is not on the project {project}.")
        option = next((o for o in options if normalize(o) == normalize(status)), None)
        if option is None:
            raise GitHubError(f"The Status field has no option {status}.")
        statuses = issue.project_statuses | {project: option}
        self.issues[index] = replace(issue, project_statuses=statuses)

    async def close_issue(
        self, repo: str, number: int, reason: CloseReason, duplicate_of: str | None = None
    ) -> None:
        key = self.issues[self._writable(repo, number)].key
        if duplicate_of is not None:
            self._index(*parse_key(duplicate_of))
        self.closed.add(key)
        self.close_reasons[key] = (reason, duplicate_of)

    async def reopen_issue(self, repo: str, number: int) -> None:
        index = self._writable(repo, number)
        self.closed.discard(self.issues[index].key)
        self.issues[index] = replace(self.issues[index], closed_at=None)

    async def assign(self, repo: str, number: int, login: str) -> None:
        index = self._writable(repo, number)
        issue = self.issues[index]
        if login not in issue.assignees:
            self.issues[index] = replace(issue, assignees=(*issue.assignees, login))

    def _index(self, repo: str, number: int) -> int:
        for index, issue in enumerate(self.issues):
            if (issue.repo, issue.number) == (repo, number):
                return index
        raise GitHubError(f"Could not resolve to an Issue with the number of {number}.")

    def _writable(self, repo: str, number: int) -> int:
        if repo in self.read_only:
            raise GitHubError(f"Resource not accessible by integration: {repo}")
        return self._index(repo, number)

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
                case "closed" if value.startswith(">="):
                    if issue.key not in self.closed or self._closed_on(issue) < value[2:]:
                        return False
                case "label":
                    if value not in issue.labels:
                        return False
                case "milestone":
                    if issue.milestone != value:
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

    async def repo_milestones(self, repo: str) -> list[Milestone]:
        repo = self._resolve(repo)
        milestones = []
        for title in self.milestones.get(repo, []):
            keys = [i.key for i in self.issues if (i.repo, i.milestone) == (repo, title)]
            closed = sum(key in self.closed for key in keys)
            milestones.append(Milestone(repo, title, len(keys) - closed, closed))
        return milestones

    def _resolve(self, repo: str) -> str:
        """The repo's own name: GitHub matches names ignoring case."""
        known = {
            *self.labels,
            *self.projects,
            *self.milestones,
            *(issue.repo for issue in self.issues),
        }
        for name in known:
            if name.casefold() == repo.casefold():
                return name
        raise GitHubError(f"Could not resolve to a Repository with the name '{repo}'.")

    def _closed_on(self, issue: Issue) -> str:
        """The UTC date `closed:` compares; one closed via `closed` this session is today."""
        return (issue.closed_at or datetime.now(UTC).isoformat())[:10]
