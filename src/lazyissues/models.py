"""Domain records shared by every layer. Names follow CONTEXT.md."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Issue:
    repo: str  # "owner/name"
    number: int
    title: str
    url: str
    assignees: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()
    closed: bool = False
    # The issue's Status on each project it is on, keyed by project "owner/number".
    project_statuses: dict[str, str] = field(default_factory=dict, hash=False)

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def ref(self) -> str:
        """Short form shown in lists: `name#number`."""
        return f"{self.repo.split('/', 1)[1]}#{self.number}"
