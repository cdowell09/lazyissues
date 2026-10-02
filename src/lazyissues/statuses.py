"""Status rules: the one place statuses are interpreted (ADR 0001)."""

import re
from dataclasses import dataclass

from lazyissues.config import Config, Repo
from lazyissues.models import Issue

NO_STATUS = "No status"


def normalize(name: str) -> str:
    """Status names match ignoring case and treating `-`, `_` and spaces alike."""
    return re.sub(r"[-_\s]+", " ", name).strip().casefold()


_DONE_OPTIONS = {"done", "closed", "complete", "completed"}


def is_done_option(name: str) -> bool:
    """Whether a project Status option means done, so it is reached by closing, not a move."""
    return normalize(name) in _DONE_OPTIONS


@dataclass(frozen=True)
class IssueStatus:
    name: str
    ambiguous: bool = False  # several status labels; `name` is the latest in order


@dataclass(frozen=True)
class StatusGroup:
    name: str
    issues: list[Issue]


class StatusRules:
    def __init__(self, config: Config) -> None:
        self._statuses = {normalize(status.name): status for status in config.statuses}
        self._rank = {normalize(NO_STATUS): -1} | {
            key: rank for rank, key in enumerate(self._statuses)
        }
        self._repos = {repo.name.casefold(): repo for repo in config.repos}

    def status_of(self, issue: Issue) -> IssueStatus:
        # A repo outside the repo set (a saved filter's own `repo:`) reads status labels.
        repo = self._repos.get(issue.repo.casefold(), Repo(issue.repo))
        if repo.project is not None:
            return self._project_status(issue, repo.project)
        found = {normalize(label) for label in issue.labels} & self._statuses.keys()
        if not found:
            return IssueStatus(NO_STATUS)
        latest = max(found, key=self._rank.__getitem__)
        return IssueStatus(self._statuses[latest].name, ambiguous=len(found) > 1)

    def is_done(self, issue: Issue) -> bool:
        """Done is closed, in every status source."""
        return issue.closed

    def is_active(self, status: str) -> bool:
        known = self._statuses.get(normalize(status))
        return known is not None and known.active

    def group(self, issues: list[Issue]) -> list[StatusGroup]:
        """Issues by status in display order: "No status", the configured
        statuses in order, then unknown statuses in the order first seen."""
        groups: dict[str, StatusGroup] = {}
        for issue in issues:
            name = self.status_of(issue).name
            groups.setdefault(normalize(name), StatusGroup(name, [])).issues.append(issue)
        # Unknown names share the last rank; sorted() is stable, so they stay first-seen.
        return sorted(
            groups.values(), key=lambda g: self._rank.get(normalize(g.name), len(self._rank))
        )

    def _project_status(self, issue: Issue, project: str) -> IssueStatus:
        # GitHub logins ignore case, so a hand-written `project` may differ from GitHub's.
        statuses = {key.casefold(): value for key, value in issue.project_statuses.items()}
        option = statuses.get(project.casefold())
        if option is None:
            return IssueStatus(NO_STATUS)
        known = self._statuses.get(normalize(option))
        return IssueStatus(known.name if known else option)
