"""Status rules: the one place statuses are interpreted (ADR 0001)."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from lazyissues.config import Config, Repo
from lazyissues.models import Issue

NO_STATUS = "No status"
DONE = "Done"  # every closed issue's status, whatever its source says


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
        # Unknown statuses share one rank after the configured ones, and Done comes last.
        self._unknown = len(self._statuses)
        self._rank = (
            {normalize(NO_STATUS): -1}
            | {key: rank for rank, key in enumerate(self._statuses)}
            | {normalize(DONE): self._unknown + 1}
        )
        self._repos = {repo.name.casefold(): repo for repo in config.repos}
        # Each issue's status, by identity (issues are frozen), since a redraw asks for
        # it several times; `forget` empties it so it never outgrows one list's issues.
        self._resolved: dict[int, tuple[Issue, IssueStatus]] = {}

    def forget(self) -> None:
        self._resolved.clear()

    def repo_of(self, issue: Issue) -> Repo:
        # A repo outside the repo set (a saved filter's own `repo:`) reads status labels.
        return self._repos.get(issue.repo.casefold(), Repo(issue.repo))

    def status_labels(self, issue: Issue) -> list[str]:
        """The issue's labels that name a status, as spelled on the issue."""
        if self.repo_of(issue).project is not None:
            return []  # in a project-backed repo every label is an ordinary label
        return [label for label in issue.labels if normalize(label) in self._statuses]

    def status_of(self, issue: Issue) -> IssueStatus:
        if (hit := self._resolved.get(id(issue))) and hit[0] is issue:
            return hit[1]
        status = self._resolve(issue)
        self._resolved[id(issue)] = (issue, status)
        return status

    def _resolve(self, issue: Issue) -> IssueStatus:
        if self.is_done(issue):
            return IssueStatus(DONE)
        repo = self.repo_of(issue)
        if repo.project is not None:
            return self._project_status(issue, repo.project)
        found = {normalize(label) for label in self.status_labels(issue)}
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

    def reachable(self, issue: Issue, project_options: Sequence[str]) -> list[str]:
        """The statuses a move can give `issue`: every configured status in a label-backed
        repo; in a project-backed one, the project's options (pass them in board order)
        except those meaning done, named as configured where they match."""
        return self.offered(self.repo_of(issue), project_options)

    def offered(self, repo: Repo, project_options: Sequence[str]) -> list[str]:
        """The statuses `repo`'s status source offers, as `reachable` lists them."""
        if repo.project is None:
            return [status.name for status in self._statuses.values()]
        return [self._name(option) for option in project_options if not is_done_option(option)]

    def ordinary_labels(self, repo: Repo, labels: Sequence[str]) -> list[str]:
        """`labels` without `repo`'s status labels, which only a move may change."""
        if repo.project is not None:
            return list(labels)  # in a project-backed repo every label is ordinary
        return [label for label in labels if normalize(label) not in self._statuses]

    def shortcuts(self) -> dict[str, str]:
        """Each status's move shortcut, lowercase, to the status's name."""
        return {s.key.lower(): s.name for s in self._statuses.values() if s.key}

    def group(self, issues: list[Issue]) -> list[StatusGroup]:
        """Issues by status in display order: "No status", the configured
        statuses in order, unknown statuses in the order first seen, then Done."""
        groups: dict[str, StatusGroup] = {}
        for issue in issues:
            name = self.status_of(issue).name
            groups.setdefault(normalize(name), StatusGroup(name, [])).issues.append(issue)
        # sorted() is stable, so unknown statuses, which share a rank, stay first-seen.
        return sorted(
            groups.values(), key=lambda g: self._rank.get(normalize(g.name), self._unknown)
        )

    def _project_status(self, issue: Issue, project: str) -> IssueStatus:
        # GitHub logins ignore case, so a hand-written `project` may differ from GitHub's.
        statuses = {key.casefold(): value for key, value in issue.project_statuses.items()}
        option = statuses.get(project.casefold())
        return IssueStatus(NO_STATUS if option is None else self._name(option))

    def _name(self, status: str) -> str:
        """The configured name of `status`, or `status` itself when it isn't configured."""
        known = self._statuses.get(normalize(status))
        return known.name if known else status
