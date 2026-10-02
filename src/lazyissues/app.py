"""The Textual application shell: one tab per view."""

from dataclasses import replace
from pathlib import Path

from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.widgets import Footer, Header, TabbedContent, TabPane

from lazyissues import config as config_module
from lazyissues.config import Config, ConfigError, SavedFilter
from lazyissues.github import Gateway
from lazyissues.models import IssueDetail
from lazyissues.move_tracker import MoveTracker
from lazyissues.mover import Mover
from lazyissues.statuses import StatusRules
from lazyissues.store import IssueStore, snapshot_path
from lazyissues.views.filters import FilterResults, Filters, results_id
from lazyissues.views.issue_list import IssueList
from lazyissues.views.milestones import Milestones
from lazyissues.views.my_work import MyWork
from lazyissues.views.team import Team
from lazyissues.views.unassigned import Unassigned


class LazyIssuesApp(App[None]):
    TITLE = "lazyissues"
    AUTO_FOCUS = "DataTable"  # the list, so arrows and Enter work at once
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]
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
        self.config = replace(self.config, filters=filters)
        if self.config_path is None:
            return
        try:
            config_module.save(self.config, self.config_path)
        except (ConfigError, OSError) as e:
            self.notify(str(e), title="Couldn't save filters", severity="error", timeout=10)

    def action_refresh(self) -> None:
        if pane := self.query_one(TabbedContent).active_pane:
            for view in pane.query(IssueList):
                if view.display:  # Filters keeps the results of filters run earlier, hidden
                    view.reload()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            for title, view in self.tabs():
                with TabPane(title):
                    yield view
        yield Footer()
