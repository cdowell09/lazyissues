"""`StatusList`, the editor for the status list that setup and preferences share."""

from dataclasses import replace

from rich.text import Text
from textual import on
from textual.binding import Binding
from textual.message import Message
from textual.widgets import SelectionList

from lazyissues.config import Status


class StatusList(SelectionList[str]):
    """Statuses in display order, checked when active. Each edit posts `Changed`."""

    BINDINGS = [
        Binding("shift+up", "move(-1)", "Move up"),
        Binding("shift+down", "move(1)", "Move down"),
    ]

    class Changed(Message):
        def __init__(self, statuses: list[Status]) -> None:
            super().__init__()
            self.statuses = statuses

    def __init__(self, statuses: list[Status]) -> None:
        super().__init__()
        self.statuses = list(statuses)

    def on_mount(self) -> None:
        self._show(highlight=0)

    @property
    def current(self) -> Status | None:
        """The highlighted status."""
        return None if self.highlighted is None else self.statuses[self.highlighted]

    def action_move(self, step: int) -> None:
        """Move the highlighted status `step` places later (earlier when negative)."""
        if self.highlighted is None:
            return
        at = self.highlighted
        to = max(0, min(len(self.statuses) - 1, at + step))
        self.statuses.insert(to, self.statuses.pop(at))
        self._show(highlight=to)
        self.post_message(self.Changed(list(self.statuses)))

    def set_key(self, key: str | None) -> str | None:
        """Make `key` the highlighted status's move shortcut, unless another status
        has it: then return that status's name and change nothing."""
        if self.highlighted is None:
            return None
        at = self.highlighted
        # A key and its uppercase are one shortcut (the uppercase moves at once).
        owner = next(
            (s.name for s in self.statuses if key and s.key and s.key.lower() == key.lower()), None
        )
        if owner not in (None, self.statuses[at].name):
            return owner
        self.statuses[at] = replace(self.statuses[at], key=key)
        self._show(highlight=at)
        self.post_message(self.Changed(list(self.statuses)))
        return None

    @on(SelectionList.SelectionToggled)
    def _toggle_active(self, event: SelectionList.SelectionToggled) -> None:
        event.stop()
        status = self.statuses[event.selection_index]
        self.statuses[event.selection_index] = replace(status, active=not status.active)
        self.post_message(self.Changed(list(self.statuses)))

    def _show(self, highlight: int) -> None:
        with self.prevent(SelectionList.SelectionToggled, SelectionList.SelectedChanged):
            self.clear_options()
            self.add_options(
                # Status names are the user's text, never Textual markup.
                (Text(s.name + (f"  ({s.key})" if s.key else "")), s.name, s.active)
                for s in self.statuses
            )
        self.highlighted = highlight if self.statuses else None
