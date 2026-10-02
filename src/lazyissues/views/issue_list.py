"""The grouped issue list every list tab is built on.

A tab subclasses `IssueList` and supplies only its search (`QUERY`, or `queries` when
it needs a lookup first) and its `grouping`. The list owns the tab's issue store and
view state, and draws whatever `view_model.visible_groups` returns.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.reactive import var
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static

from lazyissues import search
from lazyissues.config import Config
from lazyissues.detail import IssueDetailScreen
from lazyissues.github import Gateway, GitHubError
from lazyissues.models import Issue, IssueDetail
from lazyissues.mover import Mover
from lazyissues.statuses import DONE, StatusRules
from lazyissues.store import IssueStore
from lazyissues.view_model import (
    Grouping,
    ViewState,
    by_status,
    focusable_statuses,
    visible_groups,
)

COLUMNS = ("Issue", "Title", "Status", "Assignees", "Labels")


class IssueList(Widget):
    DEFAULT_CSS = """
    IssueList #search { dock: top; display: none; }
    IssueList.-searching #search { display: block; }
    IssueList #filters { dock: bottom; color: $text-muted; }
    IssueList #refreshing { dock: bottom; color: $text-muted; display: none; }
    IssueList.-refreshing #refreshing { display: block; }
    """
    BINDINGS = [
        Binding("slash", "search", "Search"),
        Binding("escape", "clear_search", "Clear search", show=False),
        Binding("f", "focus_status(1)", "Focus status"),
        Binding("F", "focus_status(-1)", "Focus previous status", show=False),
        Binding("d", "toggle_done", "Done"),
        Binding("z", "fold", "Fold"),
        Binding("Z", "fold_all", "Fold all", show=False),
        Binding("R", "repo_filter", "Repo"),
        Binding("m", "move", "Move"),
    ]

    LABEL: ClassVar[str]  # the tab's title
    QUERY: ClassVar[str]  # GitHub search for the tab, without `is:open`/`is:closed`
    EMPTY: ClassVar[str]  # shown when the tab has no issues at all

    refreshing = var(False)
    state = var(ViewState(), init=False)

    def __init__(
        self,
        config: Config,
        github: Gateway,
        store: IssueStore,
        details: dict[str, IssueDetail],
        mover: Mover,
        *,
        id: str,
    ) -> None:
        super().__init__(id=id)
        self.config = config
        self.github = github
        self.store = store
        self.details = details  # the app's detail cache, shared by every view
        self.mover = mover  # the app's, shared by every view
        self.rules = StatusRules(config)
        # Each table row's group, and its issue (None for a header or the empty message).
        self._rows: list[tuple[str, Issue | None]] = []

    @property
    def issues(self) -> list[Issue]:
        """The listed issues in row order, without group headers."""
        return [issue for _, issue in self._rows if issue is not None]

    async def queries(self) -> list[str]:
        """The searches whose results this tab lists, merged."""
        return [self.QUERY]

    def grouping(self) -> Grouping:
        return by_status(self.rules)

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search number, title, assignee, label", id="search")
        yield DataTable(cursor_type="row")
        yield Static(id="filters")
        yield Static("Refreshing…", id="refreshing")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(*COLUMNS)
        self.mover.changed.subscribe(self, self._on_moved)
        if self.store.issues:
            self.show()
        self.reload()

    def watch_refreshing(self, refreshing: bool) -> None:
        self.set_class(refreshing, "-refreshing")

    def watch_state(self) -> None:
        self.show()

    def reload(self) -> None:
        """Refresh from GitHub in the background, unless a refresh is already running."""
        if self.refreshing:
            return
        self.refreshing = True
        self.run_worker(self._load())

    async def _load(self) -> None:
        try:
            while True:
                with_done = self.state.show_done
                await self.store.refresh(self._read(with_done))
                if with_done or not self.state.show_done:
                    break  # otherwise done was shown mid-read, so read the done issues too
        except GitHubError as e:
            title = f"Couldn't refresh {self.LABEL}"
            self.notify(str(e), title=title, severity="error", timeout=10)
            return
        finally:
            self.refreshing = False
        self.show()

    async def _read(self, with_done: bool) -> list[Issue]:
        states = ["is:open"]
        if with_done:
            since = datetime.now(UTC).date() - timedelta(days=self.config.done_window_days)
            states.append(f"is:closed closed:>={since.isoformat()}")
        searches = [
            self.github.search_issues(search.scoped(f"{query} {state}", self.config.repo_names))
            for query in await self.queries()
            for state in states
        ]
        found = await asyncio.gather(*searches)
        return list({issue.key: issue for issues in found for issue in issues}.values())

    def show(self) -> None:
        """Draw the visible groups, keeping the cursor on the same issue if it's still listed."""
        table = self.query_one(DataTable)
        row, selected = table.cursor_row, None
        if table.rows:
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.clear()
        self._rows = []
        groups = visible_groups(self.store.issues, self.grouping(), self.rules, self.state)
        for group in groups:
            header = f"{'▸ ' if group.folded else ''}{group.name} ({group.total})"
            table.add_row(Text(header, style="bold"), *[""] * (len(COLUMNS) - 1))
            self._rows.append((group.name, None))
            for issue in group.rows:
                # Team lists a shared issue under each assignee; row keys must be unique.
                key = issue.key if issue.key not in table.rows else f"{group.name}/{issue.key}"
                table.add_row(*self._cells(issue), key=key)
                self._rows.append((group.name, issue))
        if not groups:
            message = "No issues match." if self.state.filtering else self.EMPTY
            table.add_row("", message, *[""] * (len(COLUMNS) - 2))
        if selected is not None and selected in table.rows:  # group headers have no key
            row = table.get_row_index(selected)
        table.move_cursor(row=row)
        self._show_filters()

    def _cells(self, issue: Issue) -> tuple[str, ...]:
        status = self.rules.status_of(issue)
        pending = self.store.moves.pending(issue.key)
        return (
            issue.ref + (" ⚠" if status.ambiguous else "") + (f" ⋯ {pending}" if pending else ""),
            issue.title,
            status.name,
            ", ".join(issue.assignees),
            ", ".join(issue.labels),
        )

    def _show_filters(self) -> None:
        state = self.state
        parts = [
            f"search: {state.search}" if state.search else "",
            f"status: {state.focus}" if state.focus else "",
            f"repo: {state.repo}" if state.repo else "",
            f"done: last {self.config.done_window_days} days" if state.show_done else "",
        ]
        line = self.query_one("#filters", Static)
        line.update("  ·  ".join(p for p in parts if p))
        line.display = any(parts)

    def _cursor_group(self) -> str | None:
        row = self.query_one(DataTable).cursor_row
        return self._rows[row][0] if 0 <= row < len(self._rows) else None

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not 0 <= event.cursor_row < len(self._rows) or self._rows[event.cursor_row][1] is None:
            return  # a group header
        index = sum(issue is not None for _, issue in self._rows[: event.cursor_row])
        self.app.push_screen(
            IssueDetailScreen(
                self.github, self.details, self.issues, index, self.select, self.mover
            )
        )

    def select(self, issue: Issue) -> None:
        """Move the cursor to `issue`, if a refresh hasn't dropped it from the list."""
        table = self.query_one(DataTable)
        if issue.key in table.rows:
            table.move_cursor(row=table.get_row_index(issue.key))

    def _on_moved(self, _: Issue) -> None:
        self.store.apply_moves()
        self.show()

    def selected(self) -> Issue | None:
        """The issue under the cursor; None on a group header or an empty list."""
        row = self.query_one(DataTable).cursor_row
        return self._rows[row][1] if 0 <= row < len(self._rows) else None

    def action_move(self) -> None:
        if issue := self.selected():
            self.mover.pick(issue)

    def on_key(self, event: events.Key) -> None:
        if self.query_one(DataTable).has_focus and (issue := self.selected()):
            self.mover.shortcut(issue, event)

    def action_search(self) -> None:
        self.add_class("-searching")
        box = self.query_one("#search", Input)
        box.value = self.state.search
        box.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.state = replace(self.state, search=event.value)

    def on_input_submitted(self) -> None:
        self._close_search()

    def action_clear_search(self) -> None:
        self.state = replace(self.state, search="")
        self._close_search()

    def _close_search(self) -> None:
        self.remove_class("-searching")
        self.query_one(DataTable).focus()

    def action_focus_status(self, step: int) -> None:
        statuses = focusable_statuses(self.store.issues, self.rules, self.state)
        self.state = self.state.cycle_focus(statuses, step)

    def action_toggle_done(self) -> None:
        show = not self.state.show_done
        focus = None if not show and self.state.focus == DONE else self.state.focus
        self.state = replace(self.state, show_done=show, focus=focus)
        if show:
            self.reload()

    def action_fold(self) -> None:
        if (group := self._cursor_group()) is not None:
            self.state = self.state.toggle_fold(group)
            self.query_one(DataTable).move_cursor(row=self._rows.index((group, None)))

    def action_fold_all(self) -> None:
        self.state = self.state.fold_all(list(dict.fromkeys(group for group, _ in self._rows)))

    def action_repo_filter(self) -> None:
        self.state = self.state.cycle_repo(self.config.repo_names)
