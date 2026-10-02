"""My Work: open issues assigned to the current user across the repo set."""

from rich.text import Text
from textual.app import ComposeResult
from textual.reactive import var
from textual.widget import Widget
from textual.widgets import DataTable, Static

from lazyissues import search
from lazyissues.config import Config
from lazyissues.github import Gateway, GitHubError
from lazyissues.statuses import StatusRules
from lazyissues.store import IssueStore

QUERY = "is:issue is:open assignee:@me"


class MyWork(Widget):
    DEFAULT_CSS = """
    #refreshing { dock: bottom; color: $text-muted; display: none; }
    MyWork.-refreshing #refreshing { display: block; }
    """

    refreshing = var(False)

    def __init__(self, config: Config, github: Gateway, store: IssueStore) -> None:
        super().__init__(id="my-work")
        self.config = config
        self.github = github
        self.store = store
        self.rules = StatusRules(config)

    def compose(self) -> ComposeResult:
        yield DataTable(cursor_type="row")
        yield Static("Refreshing…", id="refreshing")

    def watch_refreshing(self, refreshing: bool) -> None:
        self.set_class(refreshing, "-refreshing")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Issue", "Title", "Assignees", "Labels")
        if self.store.issues:
            self.show()
        self.reload()

    def reload(self) -> None:
        """Refresh from GitHub in the background, unless a refresh is already running."""
        if self.refreshing:
            return
        self.refreshing = True
        self.run_worker(self._load())

    async def _load(self) -> None:
        query = search.scoped(QUERY, self.config.repo_names)
        try:
            await self.store.refresh(self.github.search_issues(query))
        except GitHubError as e:
            self.notify(str(e), title="Couldn't refresh My Work", severity="error", timeout=10)
            return
        finally:
            self.refreshing = False
        self.show()

    def show(self) -> None:
        """Draw the store's issues, keeping the cursor on the same issue if it's still listed."""
        issues = self.store.issues
        table = self.query_one(DataTable)
        row, selected = table.cursor_row, None
        if table.rows:
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
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
        if selected is not None and selected in table.rows:  # group headers have no key
            row = table.get_row_index(selected)
        table.move_cursor(row=row)
