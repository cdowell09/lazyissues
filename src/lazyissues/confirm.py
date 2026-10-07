"""`Confirm`, the y/n question asked before anything is thrown away."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class Confirm(ModalScreen[bool]):
    """Asks `question`; True when the user picks `yes`. The safe answer has the focus."""

    DEFAULT_CSS = """
    Confirm { align: center middle; }
    Confirm > Vertical {
        width: auto; height: auto; padding: 1 2; background: $surface; border: round $warning;
    }
    Confirm Horizontal { height: auto; margin-top: 1; }
    Confirm Button { margin-right: 1; }
    """
    AUTO_FOCUS = "#no"
    BINDINGS = [
        Binding("y", "dismiss(True)", "Yes"),
        Binding("n,escape", "dismiss(False)", "No"),
    ]

    def __init__(self, question: str, yes: str, no: str) -> None:
        super().__init__()
        self.question, self.yes, self.no = question, yes, no

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"{self.question} (y/n)", markup=False)
            with Horizontal():
                yield Button(self.yes, variant="error", id="yes", compact=True)
                yield Button(self.no, id="no", compact=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")
