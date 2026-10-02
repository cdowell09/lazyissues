"""The loaded issues, persisted as a snapshot so the next start is instant."""

import hashlib
import json
import math
import time
from collections.abc import Awaitable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import platformdirs

from lazyissues.models import Issue
from lazyissues.move_tracker import MoveTracker

# Bump whenever `Issue` changes shape. Snapshots store `Issue` fields as JSON, so
# they must stay strings, numbers, booleans, None, or tuples and dicts of those.
SNAPSHOT_VERSION = 4

# The clock reads are stamped with when requested, and moves with when confirmed:
# monotonic, and fine-grained on Windows too.
now = time.perf_counter


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir("lazyissues", appauthor=False))


def snapshot_path(directory: Path, view: str, repos: list[str]) -> Path:
    """Where one view's snapshot for this repo set lives; the repos' order doesn't matter."""
    digest = hashlib.sha256("\n".join(sorted(repos)).encode()).hexdigest()[:16]
    return directory / f"snapshot-{view}-{digest}.json"


def _issue(fields: dict[str, Any]) -> Issue:
    # JSON has no tuples; `Issue` keeps its sequences as tuples.
    values: dict[str, Any] = {k: tuple(v) if isinstance(v, list) else v for k, v in fields.items()}
    return Issue(**values)


class IssueStore:
    """The loaded issues, opened from the snapshot at `path` (none: memory only).

    `moves` is the app's move tracker, shared by every store, so that no read requested
    before a confirmed move reverts it (ADR 0003).
    """

    def __init__(self, path: Path | None = None, moves: MoveTracker | None = None) -> None:
        self.path = path
        self.moves = moves or MoveTracker()
        self.issues: list[Issue] = self._load()
        self.requested_at = -math.inf  # when the read behind `issues` was requested

    async def refresh(self, read: Awaitable[list[Issue]]) -> None:
        """Await a read from GitHub and apply it, stamped with when it was requested."""
        requested_at = now()
        self.replace(await read, requested_at)

    def replace(self, issues: list[Issue], requested_at: float) -> None:
        """Apply a read requested at `requested_at` (on `now`'s clock).

        A read requested before the one already applied is ignored. Within a read, an
        issue moved since the read was requested keeps its confirmed status.
        """
        if requested_at < self.requested_at:
            return
        self.issues = [self.moves.settle(issue, requested_at) for issue in issues]
        self.requested_at = requested_at
        self._save()

    def apply_moves(self) -> None:
        """Show the moves confirmed since the loaded read was requested."""
        self.replace(self.issues, self.requested_at)

    def _load(self) -> list[Issue]:
        """The snapshot's issues; a missing, corrupt or old-format one is discarded."""
        if self.path is None:
            return []
        try:
            snapshot = json.loads(self.path.read_text(encoding="utf-8"))
            if snapshot["version"] != SNAPSHOT_VERSION:
                return []
            return [_issue(fields) for fields in snapshot["issues"]]
        except (OSError, ValueError, LookupError, TypeError, AttributeError):
            return []

    def _save(self) -> None:
        if self.path is None:
            return
        snapshot = {"version": SNAPSHOT_VERSION, "issues": [asdict(i) for i in self.issues]}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(snapshot), encoding="utf-8")
        except OSError:
            pass  # the snapshot only speeds up the next start; this session is unaffected
