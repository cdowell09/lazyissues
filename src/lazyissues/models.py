"""Domain records shared by every layer. Names follow CONTEXT.md."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Issue:
    repo: str  # "owner/name"
    number: int
    title: str
    url: str
    assignees: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    closed: bool = False
    closed_at: str | None = None  # ISO 8601, as GitHub sends it; None while open
    # The issue's Status on each project it is on, keyed by project "owner/number".
    project_statuses: dict[str, str] = field(default_factory=dict, hash=False)

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def ref(self) -> str:
        """Short form shown in lists: `name#number`."""
        return f"{self.repo.split('/', 1)[1]}#{self.number}"


EventKind = Literal[
    "commented",
    "labeled",
    "unlabeled",
    "assigned",
    "unassigned",
    "milestoned",
    "demilestoned",
    "closed",
    "reopened",
]


@dataclass(frozen=True)
class Event:
    """One entry in an issue's activity."""

    actor: str
    at: datetime
    kind: EventKind
    # The comment body, label, assignee, milestone or close reason; empty for a reopen.
    text: str = ""


@dataclass(frozen=True)
class ProjectField:
    """A project field's value on an issue, shown read-only (e.g. Theme)."""

    project: str
    name: str
    value: str


@dataclass(frozen=True)
class IssueDetail:
    """An issue's fields, body, hierarchy, project fields and activity, oldest first."""

    issue: Issue
    milestone: str | None = None
    body: str = ""
    parent: Issue | None = None
    sub_issues: tuple[Issue, ...] = ()
    project_fields: tuple[ProjectField, ...] = ()
    activity: tuple[Event, ...] = ()

    @property
    def comments(self) -> tuple[Event, ...]:
        return tuple(event for event in self.activity if event.kind == "commented")


@dataclass(frozen=True)
class Project:
    """A GitHub Project linked to a repository."""

    ref: str  # "owner/number", as config's `project`
    title: str
    status_options: tuple[str, ...] = ()  # its Status field's options, in board order
