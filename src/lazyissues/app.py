"""The Textual application shell: one tab per view."""

from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from lazyissues.config import Config
from lazyissues.github import Gateway
from lazyissues.store import IssueStore
from lazyissues.views.my_work import MyWork


class LazyIssuesApp(App[None]):
    TITLE = "lazyissues"
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]

    def __init__(self, config: Config, github: Gateway, store: IssueStore | None = None) -> None:
        super().__init__()
        self.config = config
        self.github = github
        self.store = store or IssueStore()

    def tabs(self) -> list[tuple[str, Widget]]:
        return [
            ("My Work", MyWork(self.config, self.github, self.store)),
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
