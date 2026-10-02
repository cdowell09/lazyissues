"""Issue detail: an issue's fields, body, hierarchy, comments and activity.

Any view opens it over its list with the list's issues and the selected one; stepping
through issues here tells the view, through `select`, which issue to select.
"""

import webbrowser
from collections.abc import Callable, Sequence

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Footer, Markdown, Static

from lazyissues.github import Gateway, GitHubError
from lazyissues.models import Event, EventKind, Issue, IssueDetail

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
    fields.append(("Milestone", detail.milestone or "-"))
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


class IssueDetailScreen(ModalScreen[None]):
    """Shows `issues[index]`, from `details` at once, and refetches it every time.

    `details` is a cache shared by every view and updated with each fetch.
    """

    AUTO_FOCUS = "#detail"  # so up/down and page keys scroll it
    BINDINGS = [
        # Priority, or the scrolling body would take the arrows for horizontal scrolling.
        Binding("left", "step(-1)", "Previous", priority=True),
        Binding("right", "step(1)", "Next", priority=True),
        Binding("z", "toggle_full", "Full screen"),
        Binding("o", "open_in_browser", "Open in browser"),
        Binding("h", "toggle_activity", "Activity"),
        Binding("escape", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    IssueDetailScreen { align-horizontal: right; }
    IssueDetailScreen #detail {
        width: 65%;
        background: $surface;
        border-left: wide $primary;
        padding: 0 1;
    }
    IssueDetailScreen.full #detail { width: 100%; border-left: none; }
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
    ) -> None:
        super().__init__()
        self.github = github
        self.details = details
        self.issues = issues
        self.index = index
        self.select = select
        self.showing_activity = False
        self.error: str | None = None  # why the latest fetch of this issue failed

    @property
    def issue(self) -> Issue:
        return self.issues[self.index]

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self.show_issue()

    def show_issue(self) -> None:
        self.error = None
        self.refresh_content()
        self.run_worker(self.fetch(self.issue), group="fetch", exclusive=True)

    def refresh_content(self) -> None:
        detail = self.details.get(self.issue.key)
        issue = detail.issue if detail else self.issue
        widgets: list[Widget] = [_line(_issue_line(issue), "title")]
        if self.error:
            widgets.append(_line(self.error, "error"))
        elif detail is None:
            widgets.append(_line("Loading…", "meta"))
        render = _activity_widgets if self.showing_activity else _detail_widgets
        widgets += render(issue, detail)
        content = self.query_one("#detail", VerticalScroll)
        content.remove_children()
        content.mount_all(widgets)
        content.scroll_home(animate=False)

    async def fetch(self, issue: Issue) -> None:
        # Stepping starts a new fetch in this exclusive group, cancelling this one.
        try:
            self.details[issue.key] = await self.github.issue_detail(issue.repo, issue.number)
        except GitHubError as e:
            self.error = f"Couldn't load {issue.ref}: {e}"
        self.refresh_content()

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

    def action_toggle_activity(self) -> None:
        self.showing_activity = not self.showing_activity
        self.refresh_content()
