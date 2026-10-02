"""The move tracker: pending, confirmed and rejected moves (ADR 0003).

An issue's status changes only once GitHub confirms its move, and a read requested
before that confirmation must not put the old status back. Pure: confirmation stamps
come from the caller, on the clock `IssueStore` stamps reads with (`store.now`).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from lazyissues.models import Issue
from lazyissues.move_planner import Step, apply


@dataclass(frozen=True)
class Rejected:
    """A move GitHub refused, shown until dismissed."""

    issue: Issue
    move: str  # the target's label
    error: str


class MoveTracker:
    def __init__(self) -> None:
        self._pending: dict[str, tuple[Issue, str]] = {}  # by issue key
        self._confirmed: dict[str, list[tuple[Sequence[Step], float]]] = {}  # oldest first
        self.rejected: list[Rejected] = []  # oldest first

    def start(self, issue: Issue, move: str) -> bool:
        """Record `move` as in flight, unless `issue` already has a move in flight."""
        if issue.key in self._pending:
            return False
        self._pending[issue.key] = (issue, move)
        return True

    def pending(self, key: str) -> str | None:
        """The label of the move in flight for the issue `key`, if any."""
        pending = self._pending.get(key)
        return pending[1] if pending else None

    def confirm(self, key: str, plan: Sequence[Step], at: float) -> None:
        """GitHub confirmed the pending move, which sent `plan`, at `at`."""
        del self._pending[key]
        self._confirmed.setdefault(key, []).append((plan, at))

    def reject(self, key: str, error: str) -> None:
        issue, move = self._pending.pop(key)
        self.rejected.append(Rejected(issue, move, error))

    def dismiss(self, rejected: Rejected) -> None:
        self.rejected.remove(rejected)

    def settle(self, issue: Issue, requested_at: float) -> Issue:
        """`issue` as read at `requested_at`, with every move confirmed since applied.

        This is the stale-read rule: a read requested after a move was confirmed may set
        the issue's status; one requested before keeps that move's result. Route every read
        of an issue's status through it."""
        for plan, at in self._confirmed.get(issue.key, ()):
            if at >= requested_at:
                issue = apply(plan, issue)
        return issue
