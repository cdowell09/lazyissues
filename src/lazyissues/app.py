"""The Textual application shell: one tab per view."""

from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from lazyissues.config import Config
from lazyissues.github import Gateway
from lazyissues.models import IssueDetail
from lazyissues.store import IssueStore
from lazyissues.views.my_work import MyWork


class LazyIssuesApp(App[None]):
    TITLE = "lazyissues"
    AUTO_FOCUS = "DataTable"  # the list, so arrows and Enter work at once
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]
    # Tabs and views size to their content by default, which leaves a view no height.
    CSS = "TabbedContent, TabPane > * { height: 1fr; }"

    def __init__(self, config: Config, github: Gateway, store: IssueStore | None = None) -> None:
        super().__init__()
        self.config = config
        self.github = github
        self.store = store or IssueStore()
        self.details: dict[str, IssueDetail] = {}  # by issue key; every view's detail cache

    def tabs(self) -> list[tuple[str, Widget]]:
        return [
            ("My Work", MyWork(self.config, self.github, self.store, self.details)),
            ("Team", Static("Coming soon.")),
            ("Milestones", Static("Coming soon.")),
            ("Unassigned", Static("Coming soon.")),
            ("Filters", Static("Coming soon.")),
        ]

    def action_refresh(self) -> None:
        self.query_one(MyWork).reload()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            for title, view in self.tabs():
                with TabPane(title):
                    yield view
        yield Footer()
