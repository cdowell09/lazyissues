"""Preferences (`S`): the team roster, pinned milestones, statuses and look."""

from dataclasses import replace

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Footer, Input, OptionList, Select, Static, TextArea
from textual.widgets.option_list import Option

from lazyissues.config import Config, Preferences, is_milestone_key
from lazyissues.status_list import StatusList
from lazyissues.views.issue_list import bound_keys


class PreferencesScreen(ModalScreen[Config | None]):
    """Edits a copy of `config`; dismisses with it on Save, or None on Cancel.

    The theme previews as its highlight moves, and Cancel puts back the config's theme.
    """

    BINDINGS = [Binding("ctrl+s", "save", "Save"), Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    PreferencesScreen { align: center middle; }
    PreferencesScreen #preferences {
        width: 80;
        max-height: 95%;
        height: auto;
        background: $surface;
        border: round $primary;
        padding: 0 1;
    }
    PreferencesScreen .heading { text-style: bold; margin-top: 1; }
    PreferencesScreen .hint { color: $text-muted; }
    PreferencesScreen TextArea { height: 5; }
    PreferencesScreen OptionList, PreferencesScreen SelectionList { height: auto; max-height: 10; }
    PreferencesScreen #key { width: 24; }
    PreferencesScreen Horizontal { height: auto; margin-top: 1; }
    PreferencesScreen Button { margin-right: 1; }
    """

    def __init__(self, config: Config, tabs: list[str]) -> None:
        """`tabs` are the tab titles, in order, for the start tab."""
        super().__init__()
        self.config = config
        self.tabs = tabs

    def compose(self) -> ComposeResult:
        config = self.config
        preferences = config.preferences
        start = preferences.start_tab if preferences.start_tab in self.tabs else self.tabs[0]
        with VerticalScroll(id="preferences"):
            yield Static("Team roster", classes="heading")
            yield Static("GitHub logins, one per line.", classes="hint")
            yield TextArea("\n".join(config.team), id="team")
            yield Static("Pinned milestones", classes="heading")
            yield Static("owner/repo/title, one per line, in the order to show.", classes="hint")
            yield TextArea("\n".join(config.pinned_milestones), id="milestones")
            yield Static("Lists", classes="heading")
            yield Checkbox(
                "Show done issues when lists open", preferences.show_done, id="show-done"
            )
            yield Static("Start on", classes="hint")
            yield Select(
                [(tab, tab) for tab in self.tabs], value=start, allow_blank=False, id="start-tab"
            )
            yield Static("Statuses", classes="heading")
            yield Static(
                "Groups show in this order, and checked statuses are active. shift+↑/↓ moves"
                " the highlighted status; its move shortcut is the key below.",
                classes="hint",
            )
            yield StatusList(config.statuses)
            yield Input(placeholder="Shortcut key", max_length=1, id="key")
            yield Static("Theme", classes="heading")
            themes = OptionList(
                *(Option(name, id=name) for name in sorted(self.app.available_themes)), id="theme"
            )
            # Here, not on mount: a highlight set on mount undid any the user (or a test)
            # had already moved while the screen mounted.
            themes.highlighted = themes.get_option_index(preferences.theme)
            yield themes
            with Horizontal():
                yield Button("Save", variant="primary", action="screen.save")
                yield Button("Cancel", action="screen.cancel")
        yield Footer()

    def on_mount(self) -> None:
        self._show_key()

    @on(OptionList.OptionHighlighted, "#theme")
    def preview_theme(self, event: OptionList.OptionHighlighted) -> None:
        # The highlight as it is now, not the event's: an event that arrives late (a slow
        # machine) mustn't preview a theme the highlight has already left.
        themes = event.option_list
        if themes.highlighted is not None:
            theme = themes.get_option_at_index(themes.highlighted).id
            assert theme is not None
            self.app.theme = theme

    @on(StatusList.SelectionHighlighted)
    def _show_key(self) -> None:
        current = self.query_one(StatusList).current
        key = self.query_one("#key", Input)
        key.disabled = current is None
        with key.prevent(Input.Changed):
            key.value = current.key or "" if current else ""

    @on(Input.Changed, "#key")
    def set_key(self, event: Input.Changed) -> None:
        key = event.value.strip() or None
        if owner := self.query_one(StatusList).set_key(key):
            self.notify(f"{key} already moves to {owner}.", severity="warning")
        elif key and {key.lower(), key.upper()} & bound_keys(self.tabs):
            self.notify(
                f"{key} is already a key in the lists, the detail or for tabs, so this"
                " shortcut won't work there.",
                severity="warning",
            )

    def action_save(self) -> None:
        team = _lines(self.query_one("#team", TextArea).text)
        pinned = _lines(self.query_one("#milestones", TextArea).text)
        if bad := [name for name in pinned if not is_milestone_key(name)]:
            self.notify(f"Pin milestones as owner/repo/title, not {bad[0]}.", severity="error")
            return
        start_tab = self.query_one("#start-tab", Select).value
        assert isinstance(start_tab, str)
        self.dismiss(
            replace(
                self.config,
                team=team,
                pinned_milestones=pinned,
                statuses=list(self.query_one(StatusList).statuses),
                preferences=Preferences(
                    show_done=self.query_one("#show-done", Checkbox).value,
                    start_tab=start_tab,
                    theme=self.app.theme,
                ),
            )
        )

    def action_cancel(self) -> None:
        self.app.theme = self.config.preferences.theme
        self.dismiss(None)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]
