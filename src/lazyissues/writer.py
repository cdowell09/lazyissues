"""Writes in the app: comment, assign, create and edit, each through its form.

One `Writer` serves every view, as `Mover` does for moves. A list or the detail mixes in
`IssueActions` for `WRITE_BINDINGS`, and redraws when `changed` publishes a write GitHub
confirmed.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any

from textual.app import App
from textual.binding import Binding
from textual.signal import Signal

from lazyissues.bulk import Outcome
from lazyissues.config import Config
from lazyissues.forms.assign import AssignForm
from lazyissues.forms.comment import CommentForm
from lazyissues.forms.create import CreateForm
from lazyissues.forms.edit import EditForm
from lazyissues.forms.form import Form, Written
from lazyissues.github import Gateway, GitHubError
from lazyissues.models import Issue, IssueDetail
from lazyissues.move_planner import MoveTo, Skip
from lazyissues.move_tracker import MoveTracker
from lazyissues.mover import Mover
from lazyissues.statuses import StatusRules
from lazyissues.store import now


class Writer:
    def __init__(
        self,
        app: App[Any],
        github: Gateway,
        config: Config,
        moves: MoveTracker,
        mover: Mover,
        details: dict[str, IssueDetail],
    ) -> None:
        """`moves` is the app's move tracker, `mover` gives a new issue its status, and
        `details` is the app's detail cache."""
        self.app = app
        self.github = github
        self.config = config
        self.moves = moves
        self.mover = mover
        self.details = details
        # Publishes each write GitHub confirmed. A view holding the issue shows the new
        # copy; one that doesn't reloads if the write `regroups`, as the issue may belong
        # there now.
        self.changed: Signal[Sequence[Written]] = Signal(app, "written")
        self.last_repo: str | None = None  # where the last issue was created
        self._written_at: dict[str, float] = {}  # when each issue's latest write was sent

    def written_since(self, key: str, requested_at: float) -> bool:
        """Whether the issue `key` was written after a read requested at `requested_at`,
        so that read is older than what the app shows (ADR 0003)."""
        return self._written_at.get(key, -math.inf) > requested_at

    def comment(self, issue: Issue) -> None:
        self._open(CommentForm(self.github, issue))

    def assign(self, issue: Issue) -> None:
        self._open(AssignForm(self.github, issue, self.config.team))

    def edit(self, issue: Issue) -> None:
        self._open(EditForm(self.github, issue, StatusRules(self.config), self.details))

    def create(self, near: Issue | None) -> None:
        """Create an issue, in the last repo used, else `near`'s, else the first."""
        repos = self.config.repo_names
        candidates = (self.last_repo, near and near.repo)
        repo = next((name for name in candidates if name in repos), repos[0])
        self._open(CreateForm(self.github, self.config, repo), self._created)

    async def assign_each(self, outcomes: Sequence[Outcome], login: str) -> list[Outcome]:
        """Add `login` to the assignees of each issue `outcomes` plans for, keeping the
        others, one by one, and show GitHub's copy of each, all at once at the end; with
        GitHub's error on each it refuses."""
        done, written = [], []
        for outcome in outcomes:
            if not isinstance(outcome.plan, Skip):
                issue, sent_at = outcome.issue, now()
                try:
                    assigned = await self.github.assign(issue.repo, issue.number, login)
                except GitHubError as e:
                    outcome = replace(outcome, error=str(e))
                else:
                    # The reply has the list's fields only: a cached detail keeps the rest,
                    # and an uncached one stays uncached.
                    cached = self.details.get(assigned.key)
                    detail = replace(cached, issue=assigned) if cached else IssueDetail(assigned)
                    each = Written(detail, sent_at, AssignForm.REGROUPS)
                    written.append(self._record(each, cache=cached is not None))
            done.append(outcome)
        self.changed.publish(written)  # one redraw for the whole bulk assign
        return done

    def _open(self, form: Form, done: Callable[[Written], None] | None = None) -> None:
        def closed(written: Written | None) -> None:
            if written is not None:
                (done or self._show)(written)

        self.app.push_screen(form, closed)

    def _settled(self, written: Written) -> Written:
        """`written` with the moves confirmed after it was sent applied (ADR 0003)."""
        settled = self.moves.settle(written.detail.issue, written.sent_at)
        return replace(written, detail=replace(written.detail, issue=settled))

    def _record(self, written: Written, *, cache: bool = True) -> Written:
        """Settle `written` and note it: when it was sent, and its detail in the cache
        (`cache` False leaves an uncached detail uncached)."""
        written = self._settled(written)
        key = written.detail.issue.key
        if cache:
            self.details[key] = written.detail
        self._written_at[key] = written.sent_at
        return written

    def _show(self, written: Written) -> None:
        self.changed.publish([self._record(written)])

    def _created(self, written: Written) -> None:
        issue = written.detail.issue
        self.last_repo = issue.repo
        self.app.notify(f"Created {issue.ref}.")
        self._show(written)
        if written.status:
            self.mover.move(issue, MoveTo(written.status))


# The writing keys. A host lists them in its own BINDINGS: Textual only gathers
# BINDINGS from widget classes, and `IssueActions` is a plain mixin.
WRITE_BINDINGS = [
    Binding("C", "comment", "Comment"),
    Binding("a", "assign", "Assign"),
    Binding("c", "create", "New issue"),
    Binding("e", "edit", "Edit"),
]


class IssueActions:
    """The actions of `WRITE_BINDINGS`, for the `selected` issue.

    A list or the detail mixes this in, adds `WRITE_BINDINGS` and sets `writer`. A plain
    class, not a widget, so it can't come between a screen and `Screen` in the MRO.
    """

    writer: Writer

    def selected(self) -> Issue | None:
        raise NotImplementedError

    def action_comment(self) -> None:
        if issue := self.selected():
            self.writer.comment(issue)

    def action_assign(self) -> None:
        if issue := self.selected():
            self.writer.assign(issue)

    def action_create(self) -> None:
        self.writer.create(self.selected())

    def action_edit(self) -> None:
        if issue := self.selected():
            self.writer.edit(issue)
