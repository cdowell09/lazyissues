"""The move picker: the moves an issue can make, and the original of a duplicate."""

from collections.abc import Mapping, Sequence

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static

from lazyissues.models import CloseReason, Issue
from lazyissues.move_planner import Close, MoveTo, Target, duplicate_key
from lazyissues.statuses import normalize

_DUPLICATE = Close(CloseReason.DUPLICATE)


class MovePicker(ModalScreen[Target | None]):
    """Dismisses with the chosen target, or None when cancelled.

    A status's shortcut key highlights its move; the uppercase key takes it at once.
    """

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    DEFAULT_CSS = """
    MovePicker { align: center middle; }
    MovePicker #picker {
        width: 50; height: auto; padding: 0 1;
        background: $surface; border: wide $primary;
    }
    MovePicker .title { text-style: bold; }
    MovePicker #duplicate { display: none; }
    MovePicker.-duplicate #duplicate { display: block; }
    MovePicker #hint { color: $text-muted; }
    """

    def __init__(
        self,
        issue: Issue,
        targets: Sequence[Target],
        current: str,
        shortcuts: Mapping[str, str],
        highlight: Target | None = None,
    ) -> None:
        super().__init__()
        self.issue = issue
        self.targets = list(targets)
        self.current = current  # the issue's status
        self.shortcuts = shortcuts  # key -> status, as `StatusRules.shortcuts`
        self.keys = {status: key for key, status in shortcuts.items()}
        self.highlight = self.targets.index(highlight) if highlight in self.targets else 0

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static(Text(f"Move {self.issue.ref}"), classes="title")
            yield OptionList(*(self._prompt(target) for target in self.targets))
            yield Input(placeholder="Duplicate of: 12, repo#12 or owner/repo#12", id="duplicate")
            yield Static("Enter moves · Esc cancels", id="hint")

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.highlighted = self.highlight
        options.focus()

    def _prompt(self, target: Target) -> Text:
        prompt = Text(target.label)
        if isinstance(target, MoveTo):
            if key := self.keys.get(target.status):
                prompt.append(f"  ({key})", style="bold")
            if normalize(target.status) == normalize(self.current) and not self.issue.closed:
                prompt.append(" · current", style="dim")
        return prompt

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        target = self.targets[event.option_index]
        if target == _DUPLICATE:
            self.add_class("-duplicate")
            self.query_one("#hint", Static).update("Which issue does it duplicate?")
            self.query_one(Input).focus()
        else:
            self.dismiss(target)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        key = duplicate_key(self.issue, event.value)
        if key is None:
            self.query_one("#hint", Static).update(f"{event.value!r} isn't an issue.")
        else:
            self.dismiss(Close(CloseReason.DUPLICATE, key))

    def on_key(self, event: events.Key) -> None:
        if not self.query_one(OptionList).has_focus or not event.character:
            return
        status = self.shortcuts.get(event.character.lower())
        if status is None or MoveTo(status) not in self.targets:
            return
        event.stop()
        if event.character.isupper():
            self.dismiss(MoveTo(status))
        else:
            self.query_one(OptionList).highlighted = self.targets.index(MoveTo(status))
