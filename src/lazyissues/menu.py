"""`Menu`, the option list for every picker."""

from textual import events
from textual.widgets import OptionList


class Menu(OptionList):
    """An option list where a click chooses an option and a click on the chosen one takes
    it, as Enter does. (Textual's takes an option on the first click.)"""

    def on_click(self, event: events.Click) -> None:
        option = event.style.meta.get("option")
        if option is not None and option != self.highlighted:
            event.prevent_default()  # skip the option list's own click handling
            self.highlighted = option
