"""Issue detail: an issue's fields, body, hierarchy, comments and activity.

Any view opens it over its list with the list's issues and the selected one; stepping
through issues here tells the view, through `select`, which issue to select.
"""

import asyncio
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import replace
from functools import partial

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Footer, Markdown, Static

from lazyissues.clipboard import COPY
from lazyissues.github import Gateway, GitHubError
from lazyissues.models import Event, EventKind, Issue, IssueDetail
from lazyissues.mover import Mover
from lazyissues.store import now
from lazyissues.writer import WRITE_BINDINGS, IssueActions, Writer

_ACTIONS: dict[EventKind, str] = {
    "commented": "commented",
    "labeled": "added label {}",
    "unlabeled": "removed label {}",
    "assigned": "assigned {}",
    "unassigned": "unassigned {}",
    "milestoned": "added this to milestone {}",
    "demilestoned": "removed this from milestone {}",
    "closed": "closed this as {}",
    "reopened": "reopened this",
}


def _line(text: str, classes: str = "") -> Static:
    # GitHub text is never Textual markup: `[bug]` in a title must show as typed.
    return Static(text, markup=False, classes=classes)


def _issue_line(issue: Issue) -> str:
    return f"{issue.ref}  {issue.title}"


def _fields(fields: list[tuple[str, str]]) -> Static:
    return _line("\n".join(f"{name}: {value}" for name, value in fields), "fields")


def _when(event: Event) -> str:
    return event.at.astimezone().strftime("%Y-%m-%d %H:%M")


def _detail_widgets(issue: Issue, detail: IssueDetail | None) -> list[Widget]:
    fields = [
        ("State", "Closed" if issue.closed else "Open"),
        ("Assignees", ", ".join(issue.assignees) or "-"),
        ("Labels", ", ".join(issue.labels) or "-"),
    ]
    if detail is None:  # only the list's copy so far
        return [_fields(fields)]
    fields.append(("Milestone", detail.issue.milestone or "-"))
    if detail.parent:
        fields.append(("Parent", _issue_line(detail.parent)))
    fields += [(f"{field.name} ({field.project})", field.value) for field in detail.project_fields]
    widgets: list[Widget] = [
        _fields(fields),
        Markdown(detail.body) if detail.body else _line("No description.", "meta"),
    ]
    if detail.sub_issues:
        widgets.append(_line("Sub-issues", "heading"))
        widgets += [_line(_issue_line(sub)) for sub in detail.sub_issues]
    widgets.append(_line(f"Comments ({len(detail.comments)})", "heading"))
    for comment in detail.comments:
        widgets += [_line(f"{comment.actor} · {_when(comment)}", "meta"), Markdown(comment.text)]
    return widgets


def _activity_widgets(issue: Issue, detail: IssueDetail | None) -> list[Widget]:
    if detail is None:
        return []
    widgets: list[Widget] = [_line("Activity", "heading")]
    for event in detail.activity:
        action = _ACTIONS[event.kind].format(event.text)
        widgets.append(_line(f"{_when(event)}  {event.actor} {action}", "meta"))
        if event.kind == "commented":
            widgets.append(Markdown(event.text))
    if not detail.activity:
        widgets.append(_line("No activity.", "meta"))
    return widgets


_FIRST_SCREEN = 8  # Markdown blocks drawn at once, and in each batch after; a screenful
_PAUSE = 0.1


def _first_screen(widgets: list[Widget]) -> tuple[list[Widget], list[Widget]]:
    """Splits `widgets` after the first `_FIRST_SCREEN` Markdown blocks."""
    blocks = 0
    for position, widget in enumerate(widgets):
        blocks += isinstance(widget, Markdown)
        if blocks > _FIRST_SCREEN:
            return widgets[:position], widgets[position:]
    return widgets, []


class IssueDetailScreen(IssueActions, ModalScreen[None]):
    """Shows `issues[index]`, from `details` at once, and refetches it every time.

    `details` is a cache shared by every view and updated with each fetch.
    """

    AUTO_FOCUS = "#detail"  # so up/down and page keys scroll it
    BINDINGS = [
        *WRITE_BINDINGS,
        # Priority, or the scrolling body would take the arrows for horizontal scrolling.
        Binding("left", "step(-1)", "Previous", priority=True),
        Binding("right", "step(1)", "Next", priority=True),
        Binding("z", "toggle_full", "Full screen"),
        Binding("o", "open_in_browser", "Open in browser"),
        Binding("h", "toggle_activity", "Activity"),
        Binding("m", "move", "Move"),
        Binding("escape", "dismiss", "Close"),
        COPY,
    ]

    DEFAULT_CSS = """
    IssueDetailScreen { align-horizontal: right; }
    IssueDetailScreen #panel {
        width: 65%;
        background: $surface;
        border-left: wide $primary;
    }
    IssueDetailScreen.full #panel { width: 100%; border-left: none; }
    IssueDetailScreen #close {
        min-width: 0; padding: 0 1; color: $text-error; background: transparent; text-style: bold;
    }
    IssueDetailScreen #detail { padding: 0 1; }
    IssueDetailScreen .title { text-style: bold; }
    IssueDetailScreen .heading { text-style: bold; margin-top: 1; }
    IssueDetailScreen .meta { color: $text-muted; }
    IssueDetailScreen .error { color: $error; }
    IssueDetailScreen .fields { margin: 1 0; }
    IssueDetailScreen Markdown { padding: 0; }
    """

    def __init__(
        self,
        github: Gateway,
        details: dict[str, IssueDetail],
        issues: Sequence[Issue],
        index: int,
        select: Callable[[Issue], None],
        mover: Mover,
        writer: Writer,
    ) -> None:
        super().__init__()
        self.github = github
        self.details = details
        self.issues = list(issues)
        self.mover = mover
        self.writer = writer
        self.index = index
        self.select = select
        self.showing_activity = False
        self.error: str | None = None  # why the latest fetch of this issue failed
        self._draws = 0  # counts redraws, so a fill queued by an earlier one can tell

    @property
    def issue(self) -> Issue:
        return self.issues[self.index]

    def compose(self) -> ComposeResult:
        with Vertical(id="panel"):
            yield Button("\\[×]", id="close", compact=True, action="screen.dismiss")
            yield VerticalScroll(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.mover.changed.subscribe(self, self.on_changed)
        self.writer.changed.subscribe(
            self, lambda writes: self.on_changed([w.detail.issue for w in writes])
        )
        self.show_issue()

    def on_changed(self, changed: Sequence[Issue]) -> None:
        latest = {issue.key: issue for issue in changed}
        self.issues = [latest.get(issue.key, issue) for issue in self.issues]
        if self.issue.key in latest:  # another issue's change must not redraw or scroll us
            self.refresh_content()

    def show_issue(self) -> None:
        self.error = None
        self.refresh_content()
        self.run_worker(self.fetch(self.issue), group="fetch", exclusive=True)

    def refresh_content(self) -> None:
        detail = self.details.get(self.issue.key)
        issue = detail.issue if detail else self.issue
        widgets: list[Widget] = [_line(_issue_line(issue), "title")]
        if pending := self.mover.moves.pending(issue.key):
            widgets.append(_line(f"⋯ {pending}", "meta"))
        if self.error:
            widgets.append(_line(self.error, "error"))
        elif detail is None:
            widgets.append(_line("Loading…", "meta"))
        render = _activity_widgets if self.showing_activity else _detail_widgets
        widgets += render(issue, detail)
        self._draws += 1
        content = self.query_one("#detail", VerticalScroll)
        self.workers.cancel_group(self, "fill")  # a half-filled earlier draw must not add to this
        content.remove_children()
        first, rest = _first_screen(widgets)
        content.mount_all(first)
        content.scroll_home(animate=False)
        if rest:
            # After the first screen has been laid out and painted, not before.
            # The fill is queued, so a redraw before it starts can't cancel it: `fill` checks
            # for that itself.
            fill = partial(self.fill, self._draws, content, rest)
            self.call_after_refresh(self.run_worker, fill, group="fill", exclusive=True)

    async def fill(self, draw: int, content: VerticalScroll, widgets: list[Widget]) -> None:
        """Mount `widgets` after the first screen of `draw`, unless another draw replaced it.

        Markdown costs ~15ms a block to mount, so the rest comes in batches that
        redrawing, stepping or closing cancels."""
        for start in range(0, len(widgets), _FIRST_SCREEN):
            await asyncio.sleep(_PAUSE)  # lets the screen paint
            if draw != self._draws:
                return
            await content.mount_all(widgets[start : start + _FIRST_SCREEN])

    async def fetch(self, issue: Issue) -> None:
        # Stepping starts a new fetch in this exclusive group, cancelling this one.
        requested_at = now()
        before = self.details.get(issue.key)
        try:
            detail = await self.github.issue_detail(issue.repo, issue.number)
        except GitHubError as e:
            self.error = f"Couldn't load {issue.ref}: {e}"
        else:  # a move confirmed since the fetch was sent keeps its status (ADR 0003)
            if not self.writer.written_since(issue.key, requested_at):  # else it's stale
                moved = self.mover.moves.settle(detail.issue, requested_at)
                self.details[issue.key] = replace(detail, issue=moved)
        if self.error or before is None or self.details.get(issue.key) != before:
            self.refresh_content()  # else nothing new: don't redraw or jump to the top

    def selected(self) -> Issue:
        return self.issue

    def action_step(self, delta: int) -> None:
        index = self.index + delta
        if 0 <= index < len(self.issues):
            self.index = index
            self.select(self.issue)
            self.show_issue()

    def action_toggle_full(self) -> None:
        self.toggle_class("full")

    def action_open_in_browser(self) -> None:
        webbrowser.open(self.issue.url)

    def action_move(self) -> None:
        self.mover.pick(self.issue)

    def on_key(self, event: events.Key) -> None:
        self.mover.shortcut(self.issue, event)

    def action_toggle_activity(self) -> None:
        self.showing_activity = not self.showing_activity
        self.refresh_content()
