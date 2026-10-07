"""Domain records shared by every layer. Names follow CONTEXT.md."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal, TypedDict


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
    updated_at: str | None = None  # ISO 8601, as GitHub sends it; when it last changed
    milestone: str | None = None  # its milestone's title
    parent: str | None = None  # its parent issue's key, when it is a sub-issue
    # The issue's Status on each project it is on, keyed by project "owner/number".
    project_statuses: dict[str, str] = field(default_factory=dict, hash=False)

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def ref(self) -> str:
        """Short form shown in lists: `name#number`."""
        return _ref(self.key)

    @property
    def parent_ref(self) -> str | None:
        return self.parent and _ref(self.parent)


def _ref(key: str) -> str:
    """An issue key without its owner: `name#number`."""
    return key.split("/", 1)[1]


class CloseReason(StrEnum):
    """How an issue was closed; the values are GitHub's."""

    COMPLETED = "COMPLETED"
    NOT_PLANNED = "NOT_PLANNED"
    DUPLICATE = "DUPLICATE"

    @property
    def label(self) -> str:
        return self.lower().replace("_", " ")


def parse_key(key: str) -> tuple[str, int]:
    """The repo and number of an issue key, `owner/name#number`."""
    repo, _, number = key.partition("#")
    return repo, int(number)


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
    body: str = ""
    parent: Issue | None = None
    sub_issues: tuple[Issue, ...] = ()
    project_fields: tuple[ProjectField, ...] = ()
    activity: tuple[Event, ...] = ()

    @property
    def comments(self) -> tuple[Event, ...]:
        return tuple(event for event in self.activity if event.kind == "commented")


@dataclass(frozen=True)
class Milestone:
    """A milestone in one repo; same-titled milestones in different repos are different."""

    repo: str  # "owner/name"
    title: str
    open: int = 0  # how many of its issues are open
    closed: int = 0  # how many are done

    @property
    def key(self) -> str:
        """`owner/name/title`, as config's `pinned_milestones` lists it."""
        return f"{self.repo}/{self.title}"

    @property
    def name(self) -> str:
        """Shown as `name / title`."""
        return f"{self.repo.split('/', 1)[1]} / {self.title}"


@dataclass(frozen=True)
class Project:
    """A GitHub Project linked to a repository."""

    ref: str  # "owner/number", as config's `project`
    title: str
    status_options: tuple[str, ...] = ()  # its Status field's options, in board order


class IssueChanges(TypedDict, total=False):
    """The fields an edit changes; a field left out stays as it is on GitHub."""

    title: str
    body: str
    milestone: str | None  # None removes it
