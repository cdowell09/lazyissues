"""The grouped issue list every list tab is built on.

A tab subclasses `IssueList` and supplies only its search (`QUERY`, or `queries` when
it needs a lookup first) and its `grouping`, and may add to a group's `header`. The list
owns the tab's issue store and view state, and draws whatever
`view_model.visible_groups` returns.
"""

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingsMap
from textual.coordinate import Coordinate
from textual.keys import key_to_character
from textual.reactive import var
from textual.selection import Selection
from textual.strip import Strip
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Input, Static

from lazyissues import bulk_actions, search
from lazyissues.config import Config
from lazyissues.detail import IssueDetailScreen
from lazyissues.forms.form import Written
from lazyissues.github import Gateway, GitHubError
from lazyissues.keys import tab_bindings
from lazyissues.models import Issue, IssueDetail
from lazyissues.mover import Mover
from lazyissues.statuses import DONE, StatusRules
from lazyissues.store import IssueStore
from lazyissues.view_model import (
    Group,
    Grouping,
    Row,
    ViewState,
    age,
    by_status,
    focusable_statuses,
    visible_groups,
)
from lazyissues.writer import LINK_BINDINGS, WRITE_BINDINGS, IssueActions, Writer

# The first column is each row's checkbox, a column rather than Textual's row label: a
# redraw drops the label column until the table is next idle, and a click in between
# landed on the cell beside it. Columns stay put.
COLUMNS = ("", "Issue", "Title", "Status", "Assignees", "Labels")
FOLDED, UNFOLDED = "▸", "▾"  # the fold arrows; a click on one folds or unfolds
SEARCH_PAUSE = 0.1  # seconds after the last keystroke before a search filters
AGE_TICK = 60  # seconds between redraws of the data's age; past a minute it counts minutes
CHECKED, UNCHECKED = "☑", "☐"  # whether a row is selected; a click toggles it
DIM = Style(dim=True)  # what supports a row's text: counts, tree lines, a far parent
# How a row stands out, by its table component class (`IssueTable.highlights`).
GROUP_ROW, CHECKED_ROW = "issue-table--group", "issue-table--checked"


def arrow(folded: bool | None) -> str:
    """A row's fold arrow and the space after it; blank for an issue with nothing to fold."""
    return "  " if folded is None else f"{FOLDED if folded else UNFOLDED} "


# Issue rows start one arrow in, so an issue's arrow sits under its group's name and refs
# line up whether or not an issue has sub-issues to fold.
INDENT = arrow(None)


class IssueTable(DataTable):
    """The list's table, whose text a drag selects. (Textual's table has no selection.)

    Clicks are the list's to handle (`IssueList.on_click`): Textual's table opens a row
    only on a second click on the same cell.
    """

    ALLOW_SELECT = True
    COMPONENT_CLASSES: ClassVar[set[str]] = {GROUP_ROW, CHECKED_ROW}
    DEFAULT_CSS = """
    IssueTable > .issue-table--group { background: $foreground 8%; text-style: bold; }
    IssueTable > .issue-table--checked { background: $accent 20%; }
    /* Bold marks a group header, so the cursor only colors its row. */
    IssueTable:focus > .datatable--cursor { text-style: none; }
    """
    # Each row's component class, if it has one, by row index. Set it only with the rows
    # themselves (`IssueList.show`): Textual caches drawn rows until they change.
    highlights: Sequence[str | None] = ()

    def _get_row_style(self, row_index: int, base_style: Style) -> Style:
        # Textual's private hook (as of 8.2, hence `textual<9`) for the style of a row
        # past its fixed checkbox column. A test of checked rows' backgrounds guards it.
        style = super()._get_row_style(row_index, base_style)
        if 0 <= row_index < len(self.highlights) and (name := self.highlights[row_index]):
            style += self.get_component_rich_style(name)
        return style

    def on_click(self, event: events.Click) -> None:
        event.prevent_default()

    def render_line(self, y: int) -> Strip:
        # Offsets tell a drag which character is where; selections are in on-screen lines.
        line = super().render_line(y).apply_offsets(0, y)
        span = self.text_selection.get_span(y) if self.text_selection else None
        if span is None:
            return line
        end = line.cell_length if span[1] == -1 else min(span[1], line.cell_length)
        start = min(span[0], end)
        before, selected, after = line.divide([start, end, line.cell_length])
        # Only the selection's background: its text color is usually "transparent".
        style = Style(bgcolor=self.selection_style.bgcolor)
        selected = Strip(Segment.apply_style(selected, post_style=style), selected.cell_length)
        return Strip.join([before, selected, after])

    def get_selection(self, selection: Selection) -> tuple[str, str]:
        lines = [self.render_line(y).text.rstrip() for y in range(self.size.height)]
        return selection.extract("\n".join(lines)), "\n"


class IssueList(IssueActions, Widget):
    DEFAULT_CSS = """
    IssueList #search { dock: top; display: none; }
    IssueList.-searching #search { display: block; }
    IssueList #filters { dock: bottom; color: $text-muted; }
    IssueList #refreshing { dock: bottom; color: $text-muted; display: none; }
    IssueList.-refreshing #refreshing { display: block; }
    """
    BINDINGS = [
        *WRITE_BINDINGS,
        *LINK_BINDINGS,
        Binding("slash", "search", "Search"),
        Binding("escape", "clear_search", "Clear search", show=False),
        Binding("f", "focus_status(1)", "Focus status", show=False),
        Binding("F", "focus_status(-1)", "Focus previous status", show=False),
        Binding("d", "toggle_done", "Done", show=False),
        Binding("z", "fold", "Fold", show=False),
        Binding("Z", "fold_all", "Fold all", show=False),
        Binding("R", "repo_filter", "Repo", show=False),
        Binding("m", "move", "Move"),
        Binding("space", "toggle_selected", "Select", show=False),
        Binding("A", "select_all", "Select all shown", show=False),
        Binding("u", "clear_selection", "Clear selection", show=False),
        Binding("B", "bulk", "Bulk move or assign", show=False),
    ]

    LABEL: ClassVar[str]  # the tab's title
    QUERY: ClassVar[str]  # GitHub search for the tab; states are added unless it names one
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
        writer: Writer,
        *,
        id: str,
    ) -> None:
        super().__init__(id=id)
        self.config = config
        self.github = github
        self.store = store
        self.details = details  # the app's detail cache, shared by every view
        self.mover = mover  # the app's, shared by every view
        self.writer = writer  # the app's, shared by every view
        self._loaded = False  # the first refresh has started
        self._read_again = False  # a reload was asked for during a refresh
        self._search_timer: Timer | None = None  # filters once typing pauses
        self.rules = StatusRules(config)
        self.set_reactive(IssueList.state, ViewState(show_done=config.preferences.show_done))
        # Each table row's group, and its issue's row (None for a header or the empty message).
        self._rows: list[tuple[str, Row | None]] = []
        self._groups: dict[str, Group] = {}  # the shown groups, by name
        self.error: str | None = None  # why the latest refresh failed
        self._stale = False  # changed while hidden: redraw when shown

    @property
    def issues(self) -> list[Issue]:
        """The listed issues in row order, without group headers."""
        return [row.issue for _, row in self._rows if row is not None]

    async def queries(self) -> list[str]:
        """The searches whose results this tab lists, merged."""
        return [self.QUERY]

    def grouping(self) -> Grouping:
        return by_status(self.rules)

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search number, title, assignee, label", id="search")
        yield IssueTable(cursor_type="row", fixed_columns=1)  # the checkboxes
        yield Static(id="filters")
        yield Static("Refreshing…", id="refreshing")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(*COLUMNS)
        self.mover.changed.subscribe(self, self._on_moved)
        self.writer.changed.subscribe(self, self._on_written)
        self.set_interval(AGE_TICK, self._show_filters)
        if self.store.issues:
            self.redraw()

    def on_show(self) -> None:
        if not self._loaded:  # the first refresh waits until the tab is shown
            self._loaded = True
            self.reload()
        if self._stale:
            self.show()

    def on_unmount(self) -> None:
        self.store.flush()  # the snapshot of changes still waiting to be saved

    def redraw(self) -> None:
        """Draw now if shown; a hidden tab draws when it's next shown."""
        if all(widget.display for widget in self.ancestors_with_self):
            self.show()
        else:
            self._stale = True

    def watch_refreshing(self, refreshing: bool) -> None:
        self.set_class(refreshing, "-refreshing")

    def watch_state(self, old: ViewState, new: ViewState) -> None:
        if old.selected != new.selected and replace(old, selected=new.selected) == new:
            self._show_selection()  # only checkboxes changed: no rebuild, so cursor and scroll stay
        else:
            self.show()

    def configure(self, config: Config) -> None:
        """Use `config` from now on: redraw by its statuses and refresh with its roster.

        A changed done default shows or hides done issues here too.
        """
        show_done = config.preferences.show_done
        if show_done != self.config.preferences.show_done:
            self.set_reactive(IssueList.state, replace(self.state, show_done=show_done))
        self.config = config
        self.rules = StatusRules(config)
        self.redraw()
        self.reload()

    def reload(self) -> None:
        """Refresh from GitHub in the background; during a refresh, read again after it.

        A tab not yet shown only waits for its first show, which refreshes it."""
        if not self._loaded:
            return
        if self.refreshing:
            self._read_again = True
            return
        self.refreshing = True
        self.run_worker(self._load())

    async def _load(self) -> None:
        try:
            while True:
                with_done, config = self.state.show_done, self.config
                self._read_again = False
                await self.store.refresh(self._read(with_done))
                if (
                    (with_done or not self.state.show_done)
                    and config is self.config
                    and not self._read_again
                ):
                    break  # otherwise done was shown, the config changed or a reload was
                    # asked for mid-read; read again
        except GitHubError as e:
            self.error = f"Couldn't refresh: {e}"
            title = f"Couldn't refresh {self.LABEL}"
            self.notify(str(e), title=title, severity="error", timeout=10)
        else:
            self.error = None
        finally:
            self.refreshing = False
        self.redraw()

    async def _read(self, with_done: bool) -> list[Issue]:
        since = None
        if with_done:
            since = datetime.now(UTC).date() - timedelta(days=self.config.done_window_days)
        searches = [
            self.github.search_issues(search.scoped(query, self.config.repo_names))
            for tab_query in await self.queries()
            for query in search.with_states(tab_query, since)
        ]
        found = await asyncio.gather(*searches)
        return list({issue.key: issue for issues in found for issue in issues}.values())

    def show(self) -> None:
        """Draw the visible groups, keeping the cursor on the same issue if it's still listed."""
        self._stale = False
        table = self.query_one(IssueTable)
        row, selected = table.cursor_row, None
        if table.rows:
            selected = table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
        table.clear()
        self._rows = []
        groups = visible_groups(self.store.issues, self.grouping(), self.rules, self.state)
        self._groups = {group.name: group for group in groups}
        for group in groups:
            mark = self._mark(issue.key for issue in group.issues)
            table.add_row(mark, self.header(group), *[""] * (len(COLUMNS) - 2))
            self._rows.append((group.name, None))
            for listed in group.rows:
                # Team lists a shared issue under each assignee; row keys must be unique.
                key = listed.issue.key
                key = key if key not in table.rows else f"{group.name}/{key}"
                table.add_row(self._mark([listed.issue.key]), *self._cells(listed), key=key)
                self._rows.append((group.name, listed))
        if not groups:
            # With nothing loaded, a failed refresh's error stays after its toast goes.
            message = "No issues match." if self.state.filtering else self.error or self.EMPTY
            table.add_row("", "", message, *[""] * (len(COLUMNS) - 3))
        table.highlights = self._highlights()
        if selected is not None and selected in table.rows:  # group headers have no key
            row = table.get_row_index(selected)
        table.move_cursor(row=row)
        self._show_filters()

    def _highlights(self) -> list[str | None]:
        return [
            GROUP_ROW if row is None else CHECKED_ROW if self._checked([row.issue.key]) else None
            for _, row in self._rows
        ]

    def _show_selection(self) -> None:
        """Redraw the checkboxes and checked rows' tint where they changed, in place."""
        table = self.query_one(IssueTable)
        for at, (group, row) in enumerate(self._rows):
            keys = [row.issue.key] if row else [issue.key for issue in self._groups[group].issues]
            mark = self._mark(keys)
            if table.get_cell_at(Coordinate(at, 0)).plain != mark.plain:
                table.update_cell_at(Coordinate(at, 0), mark)
        table.highlights = self._highlights()
        table.refresh()  # the tint is drawn from `highlights`, outside the cells
        self._show_filters()

    def _checked(self, keys: Iterable[str]) -> bool:
        """Whether every one of the issues `keys` is selected (and there is one)."""
        keys = set(keys)
        return bool(keys) and keys <= self.state.selected

    def _mark(self, keys: Iterable[str]) -> Text:
        return Text(CHECKED) if self._checked(keys) else Text(UNCHECKED, DIM)

    def header(self, group: Group) -> Text:
        """A group's header row; a tab may add to it."""
        return Text.assemble(arrow(group.folded), f"{group.name} ", (f"({group.total})", DIM))

    def _cells(self, row: Row) -> tuple[str | Text, ...]:
        issue = row.issue
        status = row.status
        pending = self.store.moves.pending(issue.key)
        tree = "".join("│ " if more else "  " for more in row.continues[:-1])
        if row.continues:
            tree += "├ " if row.continues[-1] else "└ "
        ref = Text.assemble(
            INDENT,
            arrow(row.folded if row.has_sub_issues else None),
            (tree, DIM),
            (f"{row.lead} → " if row.lead else "", DIM),
            issue.ref,
            " ⚠" if status.ambiguous else "",
            (f" ⋯ {pending}" if pending else "", DIM),
        )
        return (
            ref,
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
            f"selected: {len(chosen)}" if (chosen := self._chosen()) else "",
            self._age(),
        ]
        line = self.query_one("#filters", Static)
        line.update("  ·  ".join(p for p in parts if p))
        line.display = any(parts)

    def _age(self) -> str:
        """How old the loaded data is, or after a failed refresh when it's from."""
        loaded_at = self.store.loaded_at
        if loaded_at is None:
            return ""
        if self.error:
            return f"refresh failed · cached {loaded_at.astimezone():%H:%M}"
        return f"updated {age(datetime.now(UTC) - loaded_at)} ago"

    def on_click(self, event: events.Click) -> None:
        """A click on a row selects it, and on the selected row opens it. A click on a group
        header, or on a parent's fold arrow, folds or unfolds it. A click on a checkbox
        (the first column) selects its issue, or its group's, for a bulk action."""
        table = self.query_one(DataTable)
        at = event.style.meta.get("row", -1)
        if table.text_selection is not None or not 0 <= at < len(self._rows):
            return  # the release of a drag that selected text, or not on a listed row
        event.stop()
        again = at == table.cursor_row
        table.move_cursor(row=at)
        if event.style.meta.get("column") == 0:  # the checkboxes
            self.action_toggle_selected()
        elif self._rows[at][1] is None or self._on_fold_arrow(table, event):
            self.action_fold()
        elif again:
            table.action_select_cursor()

    def _on_fold_arrow(self, table: DataTable, click: events.Click) -> bool:
        widget, at = self.screen.get_widget_and_offset_at(click.screen_x, click.screen_y)
        if widget is not table or at is None:
            return False
        return table.render_line(at.y).text[at.x : at.x + 1] in (FOLDED, UNFOLDED)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if not 0 <= event.cursor_row < len(self._rows) or self._rows[event.cursor_row][1] is None:
            return  # a group header
        index = sum(issue is not None for _, issue in self._rows[: event.cursor_row])
        self.app.push_screen(
            IssueDetailScreen(
                self.github,
                self.details,
                self.issues,
                index,
                self.select,
                self.mover,
                self.writer,
            )
        )

    def select(self, issue: Issue) -> None:
        """Move the cursor to `issue`, if a refresh hasn't dropped it from the list."""
        table = self.query_one(DataTable)
        if issue.key in table.rows:
            table.move_cursor(row=table.get_row_index(issue.key))

    def _on_moved(self, moved: Sequence[Issue]) -> None:
        keys = {issue.key for issue in moved}
        if any(issue.key in keys for issue in self.store.issues):
            self.store.apply_moves()
            self.redraw()

    def _on_written(self, writes: Sequence[Written]) -> None:
        shown = [self.store.update(w.detail.issue, w.sent_at) for w in writes]
        if any(shown):
            self.redraw()
        if any(w.regroups and not on for w, on in zip(writes, shown, strict=True)):
            self.reload()  # only this tab's search knows whether an issue belongs here now

    def selected(self) -> Issue | None:
        """The issue under the cursor; None on a group header or an empty list."""
        at = self.query_one(DataTable).cursor_row
        row = self._rows[at][1] if 0 <= at < len(self._rows) else None
        return row.issue if row else None

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
        self._stop_search_timer()
        self._search_timer = self.set_timer(SEARCH_PAUSE, lambda: self._search(event.value))

    def _search(self, text: str) -> None:
        self._stop_search_timer()
        self.state = replace(self.state, search=text)

    def _stop_search_timer(self) -> None:
        if self._search_timer:
            self._search_timer.stop()
            self._search_timer = None

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._search(event.value)  # without waiting out the pause
        self._close_search()

    def action_clear_search(self) -> None:
        self._search("")
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
        """Fold the parent of the issue under the cursor, or else its group."""
        cursor = self.query_one(DataTable).cursor_row
        if not 0 <= cursor < len(self._rows):
            return
        group, row = self._rows[cursor]
        name = (row.fold_key if row else None) or group
        self.state = self.state.toggle_fold(name)
        # The cursor lands on what folded: the group's header or the parent's row.
        at = next(
            at
            for at, (in_group, row) in enumerate(self._rows)
            if in_group == group and (row.issue.key if row else in_group) == name
        )
        self.query_one(DataTable).move_cursor(row=at)

    def action_fold_all(self) -> None:
        self.state = self.state.fold_all(list(dict.fromkeys(group for group, _ in self._rows)))

    def action_repo_filter(self) -> None:
        self.state = self.state.cycle_repo(self.config.repo_names)

    def action_toggle_selected(self) -> None:
        """Select or unselect the issue under the cursor, or on a header its whole group."""
        cursor = self.query_one(DataTable).cursor_row
        if not 0 <= cursor < len(self._rows):
            return
        group, row = self._rows[cursor]
        issues = [row.issue] if row else self._groups[group].issues
        self.state = self.state.toggle_selected(issue.key for issue in issues)

    def action_select_all(self) -> None:
        """Select every issue the filters show, folded or not."""
        shown = {issue.key for group in self._groups.values() for issue in group.issues}
        self.state = replace(self.state, selected=self.state.selected | shown)

    def action_clear_selection(self) -> None:
        self.state = replace(self.state, selected=frozenset())

    def _chosen(self) -> list[Issue]:
        """The selected issues still loaded, whether the filters show them or not."""
        return [issue for issue in self.store.issues if issue.key in self.state.selected]

    def action_bulk(self) -> None:
        """Move or assign the selected issues."""
        issues = self._chosen()
        if not issues:
            self.notify("Select issues with Space, or every one shown with A.", title="Bulk")
            return
        self.app.run_worker(
            bulk_actions.run(self.mover, self.writer, issues, self.action_clear_selection)
        )


def bound_keys(tabs: Sequence[str]) -> set[str]:
    """Every key a list or the detail binds, and the app's keys for its `tabs`, which a
    status shortcut can't take over; as the character typed, where a key has one."""
    bindings = [*IssueList.BINDINGS, *IssueDetailScreen.BINDINGS, *tab_bindings(tabs)]
    return {key_to_character(key) or key for key in BindingsMap(bindings).key_to_bindings}
