"""Keybinding help (`?`), drawn from the bindings themselves so it can't drift from them."""

from collections.abc import Iterable

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Footer, Static


class KeysScreen(ModalScreen[None]):
    """Lists every binding in `sections`, footer-hidden ones too. A section is a title
    and its bindings, such as one screen's or widget's `BINDINGS`."""

    BINDINGS = [Binding("escape,question_mark", "dismiss", "Close")]
    DEFAULT_CSS = """
    KeysScreen { align: center middle; }
    KeysScreen #keys {
        width: 64;
        max-height: 90%;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 0 1;
    }
    KeysScreen .heading { text-style: bold; margin-top: 1; }
    """

    def __init__(self, sections: list[tuple[str, Iterable[BindingType]]]) -> None:
        super().__init__()
        self.sections = sections

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="keys"):
            for title, bindings in self.sections:
                yield Static(title, classes="heading")
                lines = [
                    f"{self.app.get_key_display(b):>10}  {b.description or b.action}"
                    for b in Binding.make_bindings(bindings)
                ]
                yield Static("\n".join(lines), markup=False)
        yield Footer()
