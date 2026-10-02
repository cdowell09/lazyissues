"""The move planner: turns a move into the gateway calls that make it (ADR 0001).

Pure: `Planner.plan` decides which calls to send, and each step's `apply` gives the issue
as GitHub will have it once the call succeeds. `send` hands a plan to the gateway. A bulk
move plans each issue on its own and lists the `Skip` reasons.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from lazyissues.github import Gateway
from lazyissues.models import CloseReason, Issue
from lazyissues.statuses import StatusRules, normalize


@dataclass(frozen=True)
class AddLabel:
    """Add the repo's label for status `name`, creating the label if the repo has none."""

    name: str

    async def send(self, github: Gateway, issue: Issue) -> None:
        await github.add_label(issue.repo, issue.number, self.name)

    def apply(self, issue: Issue) -> Issue:
        if any(normalize(label) == normalize(self.name) for label in issue.labels):
            return issue
        return replace(issue, labels=(*issue.labels, self.name))


@dataclass(frozen=True)
class RemoveLabels:
    names: tuple[str, ...]

    async def send(self, github: Gateway, issue: Issue) -> None:
        await github.remove_labels(issue.repo, issue.number, self.names)

    def apply(self, issue: Issue) -> Issue:
        return replace(issue, labels=tuple(n for n in issue.labels if n not in self.names))


@dataclass(frozen=True)
class AddToProject:
    project: str  # "owner/number"

    async def send(self, github: Gateway, issue: Issue) -> None:
        await github.add_to_project(issue.repo, issue.number, self.project)

    def apply(self, issue: Issue) -> Issue:
        return issue  # on the project with no status until `SetProjectStatus`


@dataclass(frozen=True)
class SetProjectStatus:
    project: str
    status: str

    async def send(self, github: Gateway, issue: Issue) -> None:
        await github.set_project_status(issue.repo, issue.number, self.project, self.status)

    def apply(self, issue: Issue) -> Issue:
        others = {
            project: status
            for project, status in issue.project_statuses.items()
            if project.casefold() != self.project.casefold()
        }
        return replace(issue, project_statuses=others | {self.project: self.status})


@dataclass(frozen=True)
class Assign:
    login: str

    async def send(self, github: Gateway, issue: Issue) -> None:
        await github.assign(issue.repo, issue.number, self.login)

    def apply(self, issue: Issue) -> Issue:
        if self.login in issue.assignees:
            return issue
        return replace(issue, assignees=(*issue.assignees, self.login))


@dataclass(frozen=True)
class Close:
    """A target, and the step that reaches it. A duplicate names the issue it duplicates."""

    reason: CloseReason
    duplicate_of: str | None = None  # "owner/repo#number"

    @property
    def label(self) -> str:
        if self.duplicate_of:
            return f"Close as duplicate of {self.duplicate_of}"
        if self.reason is CloseReason.DUPLICATE:
            return "Close as duplicate of…"
        return f"Close as {self.reason.label}"

    async def send(self, github: Gateway, issue: Issue) -> None:
        await github.close_issue(issue.repo, issue.number, self.reason, self.duplicate_of)

    def apply(self, issue: Issue) -> Issue:
        return replace(issue, closed=True)


@dataclass(frozen=True)
class Reopen:
    """A target, and the step that reaches it."""

    label = "Reopen"

    async def send(self, github: Gateway, issue: Issue) -> None:
        await github.reopen_issue(issue.repo, issue.number)

    def apply(self, issue: Issue) -> Issue:
        return replace(issue, closed=False, closed_at=None)


@dataclass(frozen=True)
class MoveTo:
    status: str

    @property
    def label(self) -> str:
        return self.status


type Step = AddLabel | RemoveLabels | AddToProject | SetProjectStatus | Assign | Close | Reopen
type Target = MoveTo | Close | Reopen


@dataclass(frozen=True)
class Skip:
    """Why a move can't be planned for an issue."""

    reason: str


def apply(plan: Sequence[Step], issue: Issue) -> Issue:
    """`issue` once every step of `plan` succeeded. Applying a plan again changes nothing."""
    for step in plan:
        issue = step.apply(issue)
    return issue


async def send(github: Gateway, issue: Issue, plan: Sequence[Step]) -> None:
    """Send `plan`'s calls in order; a `GitHubError` stops at the call GitHub rejected."""
    for step in plan:
        await step.send(github, issue)


@dataclass(frozen=True)
class Planner:
    rules: StatusRules
    viewer: str  # assigned when moving an unassigned issue to an active status
    project_options: Mapping[str, Sequence[str]]  # each project's Status options, in order

    def statuses(self, issue: Issue) -> list[str]:
        project = self.rules.repo_of(issue).project
        options = self.project_options.get(project, ()) if project else ()
        return self.rules.reachable(issue, options)

    def targets(self, issue: Issue) -> list[Target]:
        """The moves to offer for `issue`, in picker order."""
        moves: list[Target] = [MoveTo(status) for status in self.statuses(issue)]
        if issue.closed:
            return [*moves, Reopen()]
        return [*moves, *(Close(reason) for reason in CloseReason)]

    def plan(self, issue: Issue, target: Target) -> list[Step] | Skip:
        match target:
            case MoveTo(status):
                return self._move(issue, status)
            case Close() if issue.closed:
                return Skip("Already closed")
            case Close(CloseReason.DUPLICATE, None):
                return Skip("Choose the issue it duplicates")
            case Close(CloseReason.DUPLICATE, str(key)) if key.casefold() == issue.key.casefold():
                return Skip("An issue can't duplicate itself")
            case Reopen() if not issue.closed:
                return Skip("Already open")
            case _:
                return [target]

    def _move(self, issue: Issue, status: str) -> list[Step] | Skip:
        repo = self.rules.repo_of(issue)
        name = next((s for s in self.statuses(issue) if normalize(s) == normalize(status)), None)
        if name is None:
            source = f"Project {repo.project}" if repo.project else repo.name
            return Skip(f"{source} has no status {status}")
        current = self.rules.status_of(issue)
        if not issue.closed and not current.ambiguous and current.name == name:
            return Skip(f"Already in {name}")

        steps: list[Step] = [Reopen()] if issue.closed else []
        if repo.project is None:
            labels = self.rules.status_labels(issue)
            if not any(normalize(label) == normalize(name) for label in labels):
                steps.append(AddLabel(name))
            if others := tuple(label for label in labels if normalize(label) != normalize(name)):
                steps.append(RemoveLabels(others))
        else:
            projects = {project.casefold() for project in issue.project_statuses}
            if repo.project.casefold() not in projects:
                steps.append(AddToProject(repo.project))
            if current.name != name:
                steps.append(SetProjectStatus(repo.project, name))
        if self.rules.is_active(name) and not issue.assignees:
            steps.append(Assign(self.viewer))
        return steps


# "12", "#12", "repo#12", "owner/repo#12", or an issue's URL.
_REFERENCE = re.compile(r"(?:(?:(?P<owner>[\w.-]+)/)?(?P<name>[\w.-]+)(?:#|/issues/)|#)?(?P<n>\d+)")


def duplicate_key(issue: Issue, text: str) -> str | None:
    """The key of the issue `text` refers to, read relative to `issue`'s repo."""
    match = _REFERENCE.fullmatch(text.strip().removeprefix("https://github.com/"))
    if match is None:
        return None
    owner, name = issue.repo.split("/", 1)
    return f"{match['owner'] or owner}/{match['name'] or name}#{match['n']}"
