"""What every form shares: the modal frame, saving to GitHub, multiline text fields
with an `$EDITOR` round-trip, and a filterable multi-select picker."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    Select,
    SelectionList,
    Static,
    TextArea,
)

from lazyissues import editor
from lazyissues.clipboard import COPY
from lazyissues.github import Gateway, GitHubError
from lazyissues.models import IssueDetail
from lazyissues.store import now


class FormError(Exception):
    """Input the form can't send; shown like GitHub's errors."""


class Field:
    """A form field, which says whether the user `changed` it since the form filled it
    in; one the form never filled starts empty. A form asks before discarding a change."""

    @property
    def changed(self) -> bool:
        raise NotImplementedError


class TextField(Field, TextArea):
    """Multiline text: Enter submits the form, Shift+Enter or Ctrl+J starts a new line."""

    filled = ""  # the text the form last filled in

    class Submitted(Message):
        pass

    @property
    def changed(self) -> bool:
        return self.text != self.filled

    def fill(self, text: str) -> None:
        self.text = self.filled = text

    async def _on_key(self, event: events.Key) -> None:
        # Runs before TextArea's handler, which a prevented default skips.
        if event.key == "enter":
            self.post_message(self.Submitted())
        elif event.key in ("shift+enter", "ctrl+j"):
            self.replace("\n", *self.selection, maintain_selection_offset=False)
        else:
            return
        event.stop()
        event.prevent_default()


class Picker(Field, Widget):
    """Pick any number of `items`; typing in the filter narrows the list."""

    DEFAULT_CSS = """
    Picker { height: auto; }
    Picker SelectionList { height: auto; max-height: 8; }
    """

    def __init__(self, *, id: str) -> None:
        super().__init__(id=id)
        self.items: list[str] = []
        self.chosen: set[str] = set()  # kept while the filter hides some of them
        self.shown: list[str] = []
        self.filled: list[str] = []  # the picks the form last filled in

    @property
    def selected(self) -> list[str]:
        return [item for item in self.items if item in self.chosen]

    @property
    def changed(self) -> bool:
        return self.selected != self.filled

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Filter", compact=True)
        yield SelectionList[str](compact=True)

    def set_items(self, items: Iterable[str], selected: Iterable[str]) -> None:
        self.items = list(dict.fromkeys(items))
        self.chosen = set(selected)
        self.filled = self.selected
        self._show()

    def _show(self) -> None:
        text = self.query_one(Input).value.casefold()
        self.shown = [item for item in self.items if text in item.casefold()]
        options = self.query_one(SelectionList)
        options.clear_options()
        options.add_options((item, item, item in self.chosen) for item in self.shown)
        options.highlighted = 0 if self.shown else None  # so Space works on arrival

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._show()

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        event.stop()
        self.chosen = (self.chosen - set(self.shown)) | set(event.selection_list.selected)


@dataclass(frozen=True)
class Written:
    """A write GitHub confirmed, as a form closes with it."""

    detail: IssueDetail  # the issue as GitHub has it after the write
    sent_at: float  # when the write was sent, on `store.now`'s clock (ADR 0003)
    regroups: bool  # whether it can change which tabs list the issue
    status: str | None = None  # a new issue's chosen status, to set as a move


class Form(ModalScreen[Written | None]):
    """A modal form that saves to GitHub and closes with the `Written` result.

    A subclass composes its `fields`, can `load` what they offer from GitHub, and
    `save`s. A failed load or save shows the error and keeps the form open, so nothing
    typed is lost. Closing without a change returns None, and asks first when a `Field`
    changed.
    """

    REGROUPS: ClassVar[bool] = True  # whether its writes can change a tab's issues
    AUTO_FOCUS = "*"  # the first field; the app's own is its list
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "submit", "Submit", priority=True),
        # Priority, or text fields would take it as "end of line".
        Binding("ctrl+e", "open_editor", "Editor", priority=True),
        COPY,
    ]

    DEFAULT_CSS = """
    Form { align: center middle; }
    Form #form {
        width: 90%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: thick $primary;
        border-title-style: bold;
        padding: 0 1;
    }
    Form Label { margin-top: 1; color: $text-muted; }
    /* Compact fields have no border, so a shade marks where to type. */
    Form Input, Form TextField, Form Select > SelectCurrent { background: $boost; }
    Form TextField { height: 6; }
    Form #message { margin-top: 1; color: $text-muted; }
    Form #message.error { color: $error; }
    Form #buttons { height: auto; margin-top: 1; }
    Form #buttons Button { margin-right: 1; }
    """

    def __init__(self, github: Gateway, heading: str) -> None:
        super().__init__()
        self.github = github
        self.heading = heading
        self.ready = False  # loaded, so the fields hold what GitHub offers
        self.saving = False
        self.text_field: TextField | None = None  # the one `Ctrl+E` edits

    def fields(self) -> ComposeResult:
        yield from ()

    async def load(self) -> None:
        """Fill the fields from GitHub; most forms need nothing."""

    async def save(self) -> IssueDetail | None:
        """Write to GitHub; None when there is nothing to change."""
        raise NotImplementedError

    def written(self, detail: IssueDetail, sent_at: float) -> Written:
        """What the form closes with once GitHub confirms `save`."""
        return Written(detail, sent_at, self.REGROUPS)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="form", can_focus=False) as frame:
            # On the frame, so scrolling a long form never hides it.
            frame.border_title = Text(self.heading)
            yield from self.fields()
            yield Static(id="message", markup=False)
            with Horizontal(id="buttons"):
                yield Button("Submit", variant="primary", id="submit", compact=True)
                yield Button("Editor", id="editor", compact=True)
                yield Button("Cancel", id="cancel", compact=True)
        yield Footer()

    def on_mount(self) -> None:
        self.text_field = next(iter(self.query(TextField)), None)
        self.query_one("#editor").display = self.text_field is not None
        self.refresh_bindings()  # `check_action` now knows whether there's a text field
        self.reload()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        return action != "open_editor" or self.text_field is not None

    def reload(self) -> None:
        """Load again, replacing a load in progress; the form can't submit until done."""
        self.ready = False
        self.run_worker(self._load(), group="load", exclusive=True)

    async def _load(self) -> None:
        self.show_message("Loading…")
        try:
            await self.load()
        except GitHubError as e:
            self.show_message(f"Couldn't load: {e}", error=True)
            return
        self.ready = True
        self.show_message("")

    @property
    def changed(self) -> bool:
        return any(isinstance(field, Field) and field.changed for field in self.query("*"))

    def show_message(self, text: str, *, error: bool = False) -> None:
        message = self.query_one("#message", Static)
        message.update(text)
        message.set_class(error, "error")

    def action_submit(self) -> None:
        if self.ready and not self.saving:
            self.saving = True
            self.run_worker(self._save())

    async def _save(self) -> None:
        self.show_message("Saving…")
        sent_at = now()
        try:
            detail = await self.save()
        except (GitHubError, FormError) as e:
            self.show_message(str(e), error=True)
            return
        finally:
            self.saving = False
        self.dismiss(None if detail is None else self.written(detail, sent_at))

    def action_cancel(self) -> None:
        # A save on its way may already have reached GitHub; close with its result.
        if self.saving:
            return
        if not self.changed:
            self.dismiss(None)
            return

        def discard(yes: bool | None) -> None:
            if yes:
                self.dismiss(None)

        self.app.push_screen(ConfirmDiscard(), discard)

    def on_text_field_submitted(self) -> None:
        self.action_submit()

    def on_input_submitted(self) -> None:
        self.action_submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "submit":
                self.action_submit()
            case "editor":
                self.action_open_editor()
            case _:
                self.action_cancel()

    def action_open_editor(self) -> None:
        """Round-trip the text field through `$VISUAL`/`$EDITOR` for review here."""
        field = self.text_field
        if field is None:
            return
        try:
            with self.app.suspend():
                text = editor.edit(field.text)
        except (SuspendNotSupported, editor.EditorError) as e:
            self.show_message(str(e), error=True)
            return
        if text == field.text:  # an editor that returns at once never saves
            self.show_message(
                "No change came back from the editor. If it returned before you saved,"
                " set $VISUAL to a command that waits, such as `code --wait`."
            )
        field.text = text
        field.focus()


class ConfirmDiscard(ModalScreen[bool]):
    """Asks before a changed form closes; True discards the changes."""

    DEFAULT_CSS = """
    ConfirmDiscard { align: center middle; }
    ConfirmDiscard > Vertical {
        width: auto; height: auto; padding: 1 2; background: $surface; border: round $warning;
    }
    ConfirmDiscard Horizontal { height: auto; margin-top: 1; }
    ConfirmDiscard Button { margin-right: 1; }
    """
    AUTO_FOCUS = "#keep"
    BINDINGS = [
        Binding("y", "dismiss(True)", "Discard"),
        Binding("n,escape", "dismiss(False)", "Keep editing"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Discard your changes? (y/n)")
            with Horizontal():
                yield Button("Discard", variant="warning", id="discard", compact=True)
                yield Button("Keep editing", id="keep", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "discard")


def label(text: str) -> Label:
    return Label(text, markup=False)


def choices(names: Iterable[str]) -> list[tuple[str, str]]:
    """`Select` options showing each name as itself."""
    return [(name, name) for name in dict.fromkeys(names)]


@dataclass(frozen=True)
class Draft:
    """What an issue's title, body, labels and milestone fields hold."""

    title: str
    body: str
    labels: list[str]
    milestone: str | None


class IssueFields(Field, Widget):
    """The title, body, labels and milestone fields that create and edit share. The body
    and labels are fields of their own; this one is the title and milestone."""

    DEFAULT_CSS = "IssueFields { height: auto; }"

    def __init__(self) -> None:
        super().__init__()
        self.filled: tuple[str, str | None] = ("", None)  # title and milestone filled in

    @property
    def changed(self) -> bool:
        title = self.query_one("#title", Input).value
        return (title, self.query_one("#milestone", Select).selection) != self.filled

    def compose(self) -> ComposeResult:
        yield label("Title")
        yield Input(id="title", compact=True)
        yield label("Body (Shift+Enter or Ctrl+J for a new line, Ctrl+E for your editor)")
        yield TextField(id="body", compact=True)
        yield label("Labels (Space picks)")
        yield Picker(id="labels")
        yield label("Milestone")
        yield Select[str]([], prompt="No milestone", id="milestone", compact=True)

    def fill(self, title: str, body: str) -> None:
        self.query_one("#title", Input).value = title
        self.query_one(TextField).fill(body)
        self.filled = (title, self.filled[1])

    def offer(
        self,
        labels: Sequence[str],
        milestones: Sequence[str],
        *,
        picked: Sequence[str] = (),
        milestone: str | None = None,
    ) -> None:
        """Offer a repo's labels and open milestones, with `picked` and `milestone` set."""
        self.query_one(Picker).set_items([*labels, *picked], picked)
        select = self.query_one("#milestone", Select)
        select.set_options(choices([*milestones, *([milestone] if milestone else [])]))
        select.value = milestone or Select.NULL
        self.filled = (self.filled[0], milestone)

    def draft(self) -> Draft:
        title = self.query_one("#title", Input).value.strip()
        if not title:
            raise FormError("Give the issue a title.")
        return Draft(
            title,
            self.query_one(TextField).text,
            self.query_one(Picker).selected,
            self.query_one("#milestone", Select).selection,
        )


# For `?`: the form's bindings, and the keys its text fields and pickers handle.
FORM_KEYS = [
    *Form.BINDINGS,
    Binding("enter", "", "Submit, from a text field or filter"),
    Binding("shift+enter,ctrl+j", "", "New line in a text box"),
    Binding("space", "", "Pick or unpick, in a list of choices"),
]
