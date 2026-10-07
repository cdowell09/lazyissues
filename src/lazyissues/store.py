"""The loaded issues, persisted as a snapshot so the next start is instant."""

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Awaitable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import platformdirs

from lazyissues.models import Issue
from lazyissues.move_tracker import MoveTracker

# Bump whenever the snapshot or `Issue` changes shape. Snapshots store `Issue` fields as
# JSON, so they must stay strings, numbers, booleans, None, or tuples and dicts of those.
SNAPSHOT_VERSION = 6

# The clock reads are stamped with when requested, and moves with when confirmed:
# monotonic, and fine-grained on Windows too.
now = time.perf_counter

# A burst of changes (a bulk move, a refresh of every tab) saves one snapshot this long
# after the last of them.
SAVE_DELAY = 0.3


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
        self.issues: list[Issue] = []
        self.loaded_at: datetime | None = None  # when GitHub answered the read, if one has
        self._load()
        self.requested_at = -math.inf  # when the read behind `issues` was requested
        self._written: dict[str, float] = {}  # when each issue's latest write was sent
        self._saving: asyncio.TimerHandle | None = None  # the pending save, if any

    async def refresh(self, read: Awaitable[list[Issue]]) -> None:
        """Await a read from GitHub and apply it, stamped with when it was requested."""
        requested_at = now()
        issues = await read
        self.replace(issues, requested_at, loaded_at=datetime.now(UTC))

    def replace(self, issues: list[Issue], requested_at: float, loaded_at: datetime | None) -> None:
        """Apply a read requested at `requested_at` (on `now`'s clock) and answered at
        `loaded_at`.

        A read requested before the one already applied is ignored. Within a read, an
        issue moved since the read was requested keeps its confirmed status, and one
        written since keeps the copy GitHub confirmed for the write (ADR 0003).
        """
        if requested_at < self.requested_at:
            return
        loaded = {issue.key: issue for issue in self.issues}
        self.issues = [self._settle(issue, requested_at, loaded) for issue in issues]
        self.requested_at = requested_at
        self.loaded_at = loaded_at
        self._save()

    def apply_moves(self) -> None:
        """Show the moves confirmed since the loaded read was requested."""
        self.replace(self.issues, self.requested_at, self.loaded_at)

    def update(self, issue: Issue, written_at: float) -> bool:
        """Replace the loaded copy of `issue` with the one GitHub confirmed for a write
        sent at `written_at` (on `now`'s clock); False when `issue` isn't loaded here."""
        if all(loaded.key != issue.key for loaded in self.issues):
            return False
        self._written[issue.key] = written_at
        self.issues = [issue if loaded.key == issue.key else loaded for loaded in self.issues]
        self._save()
        return True

    def _settle(self, issue: Issue, requested_at: float, loaded: dict[str, Issue]) -> Issue:
        """`issue` as read at `requested_at`, unless it was written since: then the written
        copy, with only the moves confirmed after the write applied to it."""
        written = self._written.get(issue.key, -math.inf)
        if written > requested_at and issue.key in loaded:
            issue = loaded[issue.key]
        return self.moves.settle(issue, max(requested_at, written))

    def _load(self) -> None:
        """Open the snapshot; a missing, corrupt or old-format one is discarded."""
        if self.path is None:
            return
        try:
            snapshot = json.loads(self.path.read_text(encoding="utf-8"))
            if snapshot["version"] != SNAPSHOT_VERSION:
                return
            issues = [_issue(fields) for fields in snapshot["issues"]]
            stamp = snapshot["loaded_at"]
            loaded_at = datetime.fromisoformat(stamp) if stamp else None
        except (OSError, ValueError, LookupError, TypeError, AttributeError):
            return
        self.issues, self.loaded_at = issues, loaded_at

    def _save(self) -> None:
        """Save the snapshot soon, once for a burst of changes. Without a running event
        loop to wait on, save at once."""
        if self.path is None:
            return
        if self._saving:
            return
        try:
            self._saving = asyncio.get_running_loop().call_later(SAVE_DELAY, self.flush)
        except RuntimeError:
            self._write()

    def flush(self) -> None:
        """Write the snapshot now if a save is pending; call on exit."""
        if self._saving:
            self._saving.cancel()
            self._saving = None
            self._write()

    def _write(self) -> None:
        if self.path is None:
            return
        snapshot = {
            "version": SNAPSHOT_VERSION,
            "loaded_at": self.loaded_at.isoformat() if self.loaded_at else None,
            "issues": [asdict(i) for i in self.issues],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(snapshot), encoding="utf-8")
        except OSError:
            pass  # the snapshot only speeds up the next start; this session is unaffected
