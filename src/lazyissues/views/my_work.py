"""My Work: open issues assigned to the current user across the repo set."""

from rich.text import Text
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable

from lazyissues import search
from lazyissues.config import Config
from lazyissues.github import Gateway, GitHubError
from lazyissues.models import Issue
from lazyissues.statuses import StatusRules

QUERY = "is:issue is:open assignee:@me"


class MyWork(Widget):
    def __init__(self, config: Config, github: Gateway) -> None:
        super().__init__(id="my-work")
        self.config = config
        self.github = github
        self.rules = StatusRules(config)

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Issue", "Title", "Assignees", "Labels")
        self.run_worker(self.load(), exclusive=True)

    async def load(self) -> None:
        try:
            issues = await self.github.search_issues(search.scoped(QUERY, self.config.repo_names))
        except GitHubError as e:
            self.notify(str(e), title="Couldn't load My Work", severity="error", timeout=10)
            return
        self.show(issues)

    def show(self, issues: list[Issue]) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for group in self.rules.group(issues):
            table.add_row(Text(f"{group.name} ({len(group.issues)})", style="bold"), "", "", "")
            for issue in group.issues:
                marker = " ⚠" if self.rules.status_of(issue).ambiguous else ""
                table.add_row(
                    issue.ref + marker,
                    issue.title,
                    ", ".join(issue.assignees),
                    ", ".join(issue.labels),
                    key=issue.key,
                )
        if not issues:
            table.add_row("", "No open issues are assigned to you.", "", "")
