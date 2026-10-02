"""Status rules: the one place statuses are interpreted (ADR 0001)."""

import re
from dataclasses import dataclass

from lazyissues.config import Config, Repo
from lazyissues.models import Issue

NO_STATUS = "No status"


def normalize(name: str) -> str:
    """Status names match ignoring case and treating `-`, `_` and spaces alike."""
    return re.sub(r"[-_\s]+", " ", name).strip().casefold()


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
        self._rank = {key: rank for rank, key in enumerate(self._statuses)}
        self._repos = {repo.name.casefold(): repo for repo in config.repos}

    def status_of(self, issue: Issue) -> IssueStatus:
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
        return sorted(groups.values(), key=self._display_rank)

    def _display_rank(self, group: StatusGroup) -> int:
        if group.name == NO_STATUS:
            return -1
        return self._rank.get(normalize(group.name), len(self._rank))  # sorted() is stable

    def _project_status(self, issue: Issue, project: str) -> IssueStatus:
        # GitHub logins ignore case, so a hand-written `project` may differ from GitHub's.
        option = next(
            (v for k, v in issue.project_statuses.items() if k.casefold() == project.casefold()),
            None,
        )
        if option is None:
            return IssueStatus(NO_STATUS)
        known = self._statuses.get(normalize(option))
        return IssueStatus(known.name if known else option)
