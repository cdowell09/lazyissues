"""The Textual application shell: one tab per view."""

from textual.app import App, ComposeResult
from textual.widget import Widget
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from lazyissues.config import Config
from lazyissues.github import Gateway
from lazyissues.views.my_work import MyWork


class LazyIssuesApp(App[None]):
    TITLE = "lazyissues"
    BINDINGS = [("q", "quit", "Quit")]

    def __init__(self, config: Config, github: Gateway) -> None:
        super().__init__()
        self.config = config
        self.github = github

    def tabs(self) -> list[tuple[str, Widget]]:
        return [
            ("My Work", MyWork(self.config, self.github)),
            ("Team", Static("Coming soon.")),
            ("Milestones", Static("Coming soon.")),
            ("Unassigned", Static("Coming soon.")),
            ("Filters", Static("Coming soon.")),
        ]

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent():
            for title, view in self.tabs():
                with TabPane(title):
                    yield view
        yield Footer()
