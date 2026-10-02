"""Domain records shared by every layer. Names follow CONTEXT.md."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    repo: str  # "owner/name"
    number: int
    title: str
    url: str
    assignees: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def ref(self) -> str:
        """Short form shown in lists: `name#number`."""
        return f"{self.repo.split('/', 1)[1]}#{self.number}"
