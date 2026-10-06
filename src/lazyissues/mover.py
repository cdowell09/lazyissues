"""Moves in the app: picking one, sending it, and showing what GitHub answered (ADR 0003).

One `Mover` serves every view. A view binds `m` to `pick` and passes its key presses to
`shortcut`, and redraws when `changed` publishes an issue.
"""

import webbrowser
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.signal import Signal
from textual.widgets import Static

from lazyissues.bulk import Outcome
from lazyissues.github import Gateway, GitHubError
from lazyissues.models import Issue, IssueDetail
from lazyissues.move_picker import MovePicker
from lazyissues.move_planner import MoveTo, Planner, Skip, Step, Target, apply, send
from lazyissues.move_tracker import MoveTracker, Rejected
from lazyissues.statuses import StatusRules
from lazyissues.store import now


class RejectedMoveBanner(ModalScreen[None]):
    """A move GitHub refused, on screen until dismissed."""

    BINDINGS = [
        Binding("escape,enter", "dismiss", "Dismiss"),
        Binding("o", "open_in_browser", "Open in browser"),
    ]

    DEFAULT_CSS = """
    RejectedMoveBanner { align-vertical: top; }
    RejectedMoveBanner #banner {
        height: auto; padding: 0 1; background: $error 30%; border-bottom: wide $error;
    }
    RejectedMoveBanner .hint { color: $text-muted; }
    """

    def __init__(self, rejected: Rejected) -> None:
        super().__init__()
        self.rejected = rejected

    def compose(self) -> ComposeResult:
        rejected = self.rejected
        with Vertical(id="banner"):
            yield Static(
                Text(f"Couldn't move {rejected.issue.ref} to {rejected.move}: {rejected.error}"),
                id="error",
            )
            yield Static(
                "Enter or Esc dismisses · o opens the issue in your browser", classes="hint"
            )

    def action_open_in_browser(self) -> None:
        webbrowser.open(self.rejected.issue.url)


class Mover:
    def __init__(
        self,
        app: App[Any],
        github: Gateway,
        rules: StatusRules,
        moves: MoveTracker,
        details: dict[str, IssueDetail],
    ) -> None:
        """`moves` is the tracker every view's store shares; `details` the detail cache."""
        self.app = app
        self.github = github
        self.rules = rules
        self.moves = moves
        self.details = details
        # Publishes an issue as it is now, whenever a move of it starts or ends. Each view
        # then applies confirmed moves to its store (`IssueStore.apply_moves`) and redraws.
        self.changed: Signal[Sequence[Issue]] = Signal(app, "moved")
        self._viewer: str | None = None
        self._options: dict[str, list[str]] = {}  # each project's Status options

    def pick(self, issue: Issue, highlight: Target | None = None) -> None:
        """Offer `issue`'s moves, starting on `highlight`, and make the one chosen."""
        self.app.run_worker(self._pick(issue, highlight))

    def shortcut(self, issue: Issue, event: events.Key) -> None:
        """Handle a status shortcut: pick that move, or make it at once when uppercase.

        Keys the screen binds keep their binding.
        """
        if not event.character or event.key in self.app.screen.active_bindings:
            return
        status = self.rules.shortcuts().get(event.character.lower())
        if status is None:
            return
        event.stop()
        if event.character.isupper():
            self.move(issue, MoveTo(status))
        else:
            self.pick(issue, MoveTo(status))

    def move(self, issue: Issue, target: Target) -> None:
        """Make the move at once, without the picker."""
        self.app.run_worker(self._move(issue, target))

    async def _pick(self, issue: Issue, highlight: Target | None) -> None:
        if self.moves.pending(issue.key):
            self._still_moving(issue)
            return
        planner = await self.planner([issue])
        if planner is None:
            return
        picker = MovePicker(
            issue,
            planner.targets(issue),
            self.rules.status_of(issue).name,
            self.rules.shortcuts(),
            highlight,
        )
        if target := await self.app.push_screen_wait(picker):
            await self._move(issue, target, planner)

    async def _move(self, issue: Issue, target: Target, planner: Planner | None = None) -> None:
        planner = planner or await self.planner([issue])
        if planner is None:
            return
        plan = planner.plan(issue, target)
        if isinstance(plan, Skip):
            self.app.notify(plan.reason, title=f"Can't move {issue.ref}", severity="warning")
            return
        if not self.moves.start(issue, target.label):  # one move per issue at a time
            self._still_moving(issue)
            return
        self.changed.publish([issue])  # shows as pending
        rejected, moved = await self._send_started(issue, plan)
        self.changed.publish([moved])
        if rejected:
            self.app.push_screen(
                RejectedMoveBanner(rejected), lambda _: self.moves.dismiss(rejected)
            )

    async def send_each(self, outcomes: Sequence[Outcome], label: str) -> list[Outcome]:
        """Make a bulk move, `label`, of each issue `outcomes` plans for, with GitHub's
        error on each it refuses. Every move starts at once, so each shows as pending, then they
        go one by one; the lists redraw when they start and once more at the end. An issue
        with a move in flight is skipped."""
        started = [
            outcome
            if isinstance(outcome.plan, Skip) or self.moves.start(outcome.issue, label)
            else replace(
                outcome, plan=Skip(f"Still moving: {self.moves.pending(outcome.issue.key)}")
            )
            for outcome in outcomes
        ]
        self.changed.publish([o.issue for o in started if not isinstance(o.plan, Skip)])
        done = []
        changed = []
        for outcome in started:
            plan = outcome.plan
            if not isinstance(plan, Skip):
                rejected, moved = await self._send_started(outcome.issue, plan)
                changed.append(moved)
                if rejected:
                    self.moves.dismiss(rejected)  # the bulk summary lists it, not a banner each
                    outcome = replace(outcome, error=rejected.error)
            done.append(outcome)
        self.changed.publish(changed)  # one redraw for all of them
        return done

    async def _send_started(self, issue: Issue, plan: list[Step]) -> tuple[Rejected | None, Issue]:
        """Send `plan`, the move of `issue` the tracker `start`ed, and settle it: confirmed,
        or rejected and kept in the tracker until dismissed (returned, to show). Also
        returns the issue to show."""
        try:
            await send(self.github, issue, plan)
        except GitHubError as e:
            self.moves.reject(issue.key, str(e))
            return self.moves.rejected[-1], issue
        self.moves.confirm(issue.key, plan, now())
        if detail := self.details.get(issue.key):
            self.details[issue.key] = replace(detail, issue=apply(plan, detail.issue))
        return None, apply(plan, issue)

    def _still_moving(self, issue: Issue) -> None:
        pending = self.moves.pending(issue.key)
        self.app.notify(f"{issue.ref} is still moving: {pending}.", severity="warning")

    async def planner(self, issues: Sequence[Issue]) -> Planner | None:
        """A planner knowing the viewer and the project options of `issues`, or None if
        GitHub couldn't say (the error shows)."""
        projects = {project for issue in issues if (project := self.rules.repo_of(issue).project)}
        try:
            if self._viewer is None:
                self._viewer, _ = await self.github.whoami()
            for project in projects - self._options.keys():
                self._options[project] = await self.github.project_status_options(project)
        except GitHubError as e:
            moving = issues[0].ref if len(issues) == 1 else f"{len(issues)} issues"
            self.app.notify(str(e), title=f"Can't move {moving}", severity="error", timeout=10)
            return None
        return Planner(self.rules, self._viewer, self._options)
