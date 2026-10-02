"""Filters: saved filters in a sidebar, and the issues the one that ran last matches.

Each query's results are an `IssueList` of their own, with their own store and view
state, so running a filter again shows where you left it. The sidebar only edits the
list of saved filters; the app owns saving it to config.
"""

import hashlib
from collections.abc import Callable

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Label, OptionList, Static

from lazyissues import search
from lazyissues.config import Config, SavedFilter
from lazyissues.github import Gateway
from lazyissues.models import IssueDetail
from lazyissues.mover import Mover
from lazyissues.store import IssueStore
from lazyissues.view_model import ViewState
from lazyissues.views.issue_list import IssueList


def results_id(query: str) -> str:
    """The view id, and snapshot name, of a query's results."""
    return "filter-" + hashlib.sha256(query.encode()).hexdigest()[:16]


class FilterResults(IssueList):
    LABEL = "Filters"
    EMPTY = "No issues match this filter."

    def __init__(
        self,
        query: str,
        config: Config,
        github: Gateway,
        store: IssueStore,
        details: dict[str, IssueDetail],
        mover: Mover,
    ) -> None:
        super().__init__(config, github, store, details, mover, id=results_id(query))
        self.saved_query = query  # GitHub search syntax, as the user wrote it
        if search.asks_for_closed(query):  # its closed issues are what the user asked for
            self.set_reactive(IssueList.state, ViewState(show_done=True))

    async def queries(self) -> list[str]:
        return [f"is:issue {self.saved_query}"]


class Filters(Widget):
    DEFAULT_CSS = """
    Filters { layout: horizontal; }
    Filters #sidebar { width: 28; height: 1fr; }
    Filters #running { color: $text-muted; padding: 0 1; }
    Filters ContentSwitcher, Filters ContentSwitcher > * { height: 1fr; }
    """
    BINDINGS = [
        Binding("tab", "switch_pane", "Sidebar/results", show=False),
        Binding("shift+tab", "switch_pane", "Sidebar/results", show=False),
        Binding("n", "new", "New filter"),
        Binding("e", "edit", "Edit filter"),
        Binding("x", "delete", "Delete filter"),
    ]

    def __init__(
        self,
        filters: list[SavedFilter],
        results: Callable[[str], FilterResults],
        save: Callable[[list[SavedFilter]], None],
        *,
        id: str,
    ) -> None:
        """`results` makes the results list of a query; `save` keeps changed filters."""
        super().__init__(id=id)
        self.filters = list(filters)
        self.results = results
        self.save = save
        self.running: SavedFilter | None = None  # the filter whose results show

    def compose(self) -> ComposeResult:
        yield OptionList(id="sidebar")
        with Vertical():
            yield Static(id="running", markup=False)
            none = Static("No saved filters. Press n in the sidebar to add one.", id="none")
            yield ContentSwitcher(none, initial="none")

    async def on_mount(self) -> None:
        self._list_filters(0)
        if self.filters:
            await self.run_filter(self.filters[0])

    async def run_filter(self, saved: SavedFilter) -> None:
        """Show `saved`'s results, from where they were left if it ran before."""
        switcher = self.query_one(ContentSwitcher)
        view = next((v for v in self.query(FilterResults) if v.saved_query == saved.query), None)
        if view is None:
            view = self.results(saved.query)
            await switcher.mount(view)
        switcher.current = view.id
        self.running = saved
        self.query_one("#running", Static).update(f"{saved.name}  ·  {saved.query}")

    async def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        await self.run_filter(self.filters[event.option_index])

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        # The results pane has its own keys; these only edit the sidebar's filters.
        if action in ("new", "edit", "delete"):
            return self.query_one(OptionList).has_focus
        return True

    def action_switch_pane(self) -> None:
        sidebar = self.query_one(OptionList)
        shown = self.query_one(ContentSwitcher).visible_content
        if sidebar.has_focus and isinstance(shown, FilterResults):
            shown.query_one(DataTable).focus()
        else:
            sidebar.focus()

    def action_new(self) -> None:
        async def add(saved: SavedFilter | None) -> None:
            if saved is not None:
                self._change([*self.filters, saved], len(self.filters))
                await self.run_filter(saved)

        self.app.push_screen(FilterForm("New filter"), add)

    def action_edit(self) -> None:
        if (index := self.query_one(OptionList).highlighted) is None:
            return

        async def edit(saved: SavedFilter | None) -> None:
            if saved is not None:
                self._change([*self.filters[:index], saved, *self.filters[index + 1 :]], index)
                await self.run_filter(saved)

        self.app.push_screen(FilterForm("Edit filter", self.filters[index]), edit)

    def action_delete(self) -> None:
        if (index := self.query_one(OptionList).highlighted) is None:
            return

        async def delete(confirmed: bool | None) -> None:
            if confirmed:
                deleted = self.filters[index]
                self._change([*self.filters[:index], *self.filters[index + 1 :]], index)
                if deleted == self.running:
                    await self._run_highlighted()

        self.app.push_screen(ConfirmDelete(self.filters[index].name), delete)

    def _change(self, filters: list[SavedFilter], highlight: int) -> None:
        self.filters = filters
        self.save(filters)
        self._list_filters(highlight)

    def _list_filters(self, highlight: int) -> None:
        sidebar = self.query_one(OptionList)
        sidebar.set_options(Text(f.name) for f in self.filters)  # a name is never markup
        if self.filters:
            sidebar.highlighted = min(highlight, len(self.filters) - 1)

    async def _run_highlighted(self) -> None:
        index = self.query_one(OptionList).highlighted
        if index is not None:
            await self.run_filter(self.filters[index])
            return
        self.running = None
        self.query_one(ContentSwitcher).current = "none"
        self.query_one("#running", Static).update("")


class FilterForm(ModalScreen[SavedFilter | None]):
    """A saved filter's name and query; dismissed with the filter, or None if cancelled."""

    DEFAULT_CSS = """
    FilterForm { align: center middle; }
    FilterForm > Vertical {
        width: 72; height: auto; padding: 1 2; background: $surface; border: round $primary;
    }
    FilterForm #error { color: $error; }
    FilterForm Horizontal { height: auto; margin-top: 1; }
    """
    AUTO_FOCUS = "#name"  # the app's own AUTO_FOCUS would look for a list
    BINDINGS = [Binding("escape", "dismiss", "Cancel")]

    def __init__(self, title: str, saved: SavedFilter | None = None) -> None:
        super().__init__()
        self.form_title = title
        self.saved = saved or SavedFilter("", "")

    def compose(self) -> ComposeResult:
        with Vertical() as form:
            form.border_title = self.form_title
            yield Label("Name")
            yield Input(self.saved.name, id="name")
            yield Label("Query, in GitHub issue search syntax")
            yield Input(self.saved.query, placeholder="label:bug assignee:@me", id="query")
            yield Static(id="error")
            with Horizontal():
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_input_submitted(self) -> None:
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        name = self.query_one("#name", Input).value.strip()
        query = self.query_one("#query", Input).value.strip()
        if name and query:
            self.dismiss(SavedFilter(name, query))
        else:
            self.query_one("#error", Static).update("A filter needs a name and a query.")
            self.query_one("#query" if name else "#name", Input).focus()


class ConfirmDelete(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmDelete { align: center middle; }
    ConfirmDelete > Vertical {
        width: auto; height: auto; padding: 1 2; background: $surface; border: round $error;
    }
    ConfirmDelete Horizontal { height: auto; margin-top: 1; }
    """
    AUTO_FOCUS = "#keep"
    BINDINGS = [
        Binding("y", "dismiss(True)", "Delete"),
        Binding("n,escape", "dismiss(False)", "Keep"),
    ]

    def __init__(self, name: str) -> None:
        super().__init__()
        self.filter_name = name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"Delete the filter “{self.filter_name}”? (y/n)", markup=False)
            with Horizontal():
                yield Button("Delete", variant="error", id="delete")
                yield Button("Keep", id="keep")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "delete")
