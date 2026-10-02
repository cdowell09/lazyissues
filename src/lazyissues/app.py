"""The Textual application shell: one tab per view."""

from pathlib import Path

from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from lazyissues.config import Config
from lazyissues.github import Gateway
from lazyissues.models import IssueDetail
from lazyissues.move_tracker import MoveTracker
from lazyissues.mover import Mover
from lazyissues.statuses import StatusRules
from lazyissues.store import IssueStore, snapshot_path
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

    def __init__(self, config: Config, github: Gateway, cache: Path | None = None) -> None:
        """`cache` is the directory for snapshots; without one, nothing is saved."""
        super().__init__()
        self.config = config
        self.github = github
        self.cache = cache
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

        return [
            view(MyWork, "my-work"),
            view(Team, "team"),
            view(Milestones, "milestones"),
            view(Unassigned, "unassigned"),
            ("Filters", Static("Coming soon.")),
        ]

    def action_refresh(self) -> None:
        if pane := self.query_one(TabbedContent).active_pane:
            for view in pane.query(IssueList):
                view.reload()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            for title, view in self.tabs():
                with TabPane(title):
                    yield view
        yield Footer()
