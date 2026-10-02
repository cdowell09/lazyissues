"""The Textual application shell: one tab per view."""

from dataclasses import replace
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widget import Widget
from textual.widgets import DataTable, Footer, Header, TabbedContent, TabPane

from lazyissues import config as config_module
from lazyissues.config import Config, ConfigError, SavedFilter
from lazyissues.detail import IssueDetailScreen
from lazyissues.github import Gateway
from lazyissues.keys import KeysScreen
from lazyissues.models import IssueDetail
from lazyissues.move_picker import MovePicker
from lazyissues.move_tracker import MoveTracker
from lazyissues.mover import Mover, RejectedMoveBanner
from lazyissues.preferences import PreferencesScreen
from lazyissues.status_list import StatusList
from lazyissues.statuses import StatusRules
from lazyissues.store import IssueStore, snapshot_path
from lazyissues.views.filters import (
    ConfirmDelete,
    FilterForm,
    FilterResults,
    Filters,
    results_id,
)
from lazyissues.views.issue_list import IssueList
from lazyissues.views.milestones import Milestones
from lazyissues.views.my_work import MyWork
from lazyissues.views.team import Team
from lazyissues.views.unassigned import Unassigned


class LazyIssuesApp(App[None]):
    TITLE = "lazyissues"
    # Not the first list: focusing a list shows its tab, so on_mount focuses the start tab's.
    AUTO_FOCUS = None
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("S", "preferences", "Preferences"),
        Binding("question_mark", "keys", "Keys"),
    ]
    # Tabs and views size to their content by default, which leaves a view no height.
    CSS = "TabbedContent, TabPane > * { height: 1fr; }"

    def __init__(
        self,
        config: Config,
        github: Gateway,
        cache: Path | None = None,
        config_path: Path | None = None,
    ) -> None:
        """`cache` is the directory for snapshots, and `config_path` the file `config`
        came from; without them, nothing is saved."""
        super().__init__()
        self.config = config
        self.github = github
        self.cache = cache
        self.config_path = config_path
        self.details: dict[str, IssueDetail] = {}  # by issue key; every view's detail cache
        self.moves = MoveTracker()  # every view's store shares it
        self.mover = Mover(self, github, StatusRules(config), self.moves, self.details)
        self.tab_titles: list[str] = []  # set by compose

    def store(self, view: str) -> IssueStore:
        """The issue store for one view, with its own snapshot."""
        path = (
            None if self.cache is None else snapshot_path(self.cache, view, self.config.repo_names)
        )
        return IssueStore(path, self.moves)

    def tabs(self) -> list[tuple[str, Widget]]:
        def view(cls: type[IssueList], id: str) -> tuple[str, Widget]:
            store = self.store(id)
            return cls.LABEL, cls(self.config, self.github, store, self.details, self.mover, id=id)

        filters = Filters(self.config.filters, self.filter_results, self.save_filters, id="filters")
        return [
            view(MyWork, "my-work"),
            view(Team, "team"),
            view(Milestones, "milestones"),
            view(Unassigned, "unassigned"),
            ("Filters", filters),
        ]

    def filter_results(self, query: str) -> FilterResults:
        """The results list of a saved filter's query, with its own snapshot."""
        store = self.store(results_id(query))
        return FilterResults(query, self.config, self.github, store, self.details, self.mover)

    def save_filters(self, filters: list[SavedFilter]) -> None:
        """Keep `filters` as the saved filters, in the config file too when there is one."""
        self.save_config(replace(self.config, filters=filters))

    def action_refresh(self) -> None:
        if pane := self.query_one(TabbedContent).active_pane:
            for view in pane.query(IssueList):
                if view.display:  # Filters keeps the results of filters run earlier, hidden
                    view.reload()

    def action_preferences(self) -> None:
        if not isinstance(self.screen, PreferencesScreen):
            self.push_screen(PreferencesScreen(self.config, self.tab_titles), self.configure)

    def save_config(self, config: Config) -> None:
        """Make `config` the app's config and write it to `config_path`: the one place
        the running app changes its config, so no save can undo another's."""
        self.config = config
        if self.config_path is None:
            return
        try:
            config_module.save(config, self.config_path)
        except (OSError, ConfigError) as e:
            self.notify(
                f"{e}\nYour changes apply until you quit.",
                title=f"Couldn't write {self.config_path}",
                severity="error",
                timeout=10,
            )

    def configure(self, config: Config | None) -> None:
        """Save `config` from preferences and apply it live; None means cancelled.

        Each view that reads the config gets it through its own `configure`.
        """
        if config is None or config == self.config:
            return
        self.save_config(config)
        self.theme = config.preferences.theme
        self.mover.rules = StatusRules(config)  # status keys and order
        for view in self.screen_stack[0].query(IssueList):
            view.configure(config)

    def action_keys(self) -> None:
        shortcuts = [
            Binding(key, "", f"Move to {status} (uppercase: at once)")
            for key, status in self.mover.rules.shortcuts().items()
        ]
        self.push_screen(
            KeysScreen(
                [
                    ("Anywhere", self.BINDINGS),
                    ("Lists", IssueList.BINDINGS),
                    ("Moving in lists", DataTable.BINDINGS),
                    ("Issue detail", IssueDetailScreen.BINDINGS),
                    ("Status shortcuts, in lists and the detail", shortcuts),
                    ("Move picker", MovePicker.BINDINGS),
                    ("Rejected move", RejectedMoveBanner.BINDINGS),
                    ("Filters", Filters.BINDINGS),
                    ("Filter form", FilterForm.BINDINGS),
                    ("Deleting a filter", ConfirmDelete.BINDINGS),
                    ("Preferences", [*PreferencesScreen.BINDINGS, *StatusList.BINDINGS]),
                ]
            )
        )

    def compose(self) -> ComposeResult:
        tabs = self.tabs()
        self.tab_titles = [title for title, _ in tabs]
        start = self.config.preferences.start_tab
        start_id = f"tab-{self.tab_titles.index(start) if start in self.tab_titles else 0}"
        yield Header()
        with TabbedContent(initial=start_id):
            for i, (title, view) in enumerate(tabs):
                with TabPane(title, id=f"tab-{i}"):
                    yield view
        yield Footer()

    def on_mount(self) -> None:
        self.theme = self.config.preferences.theme
        # The start tab's list, so arrows and Enter work at once.
        if lists := self.query(f"#{self.query_one(TabbedContent).active} DataTable"):
            lists.first().focus()
