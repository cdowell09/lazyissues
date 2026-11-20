"""The loaded issues, persisted as a snapshot so the next start is instant."""

import asyncio
import hashlib
import json
import math
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
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
        self._committed: list[Issue] = list(self.issues)
        self.requested_at = -math.inf  # when the read behind `issues` was requested
        self._written: dict[str, tuple[Issue, float]] = {}  # latest confirmed write per key
        self._saving: asyncio.TimerHandle | None = None  # the pending save, if any

    def merge_page(self, issues: Sequence[Issue], requested_at: float) -> bool:
        """Upsert one incoming page into `issues` without pruning unseen rows, advancing
        `requested_at`, or saving.

        A page from a read requested before the one already applied is ignored. Returns
        whether `issues` changed.
        """
        if requested_at < self.requested_at:
            return False
        merged = list(self.issues)
        positions = {item.key: index for index, item in enumerate(merged)}
        for raw in issues:
            settled = self._settle(raw, requested_at)
            if (index := positions.get(settled.key)) is not None:
                merged[index] = settled
            else:
                positions[settled.key] = len(merged)
                merged.append(settled)
        changed = merged != self.issues
        self.issues = merged
        return changed

    def rollback_pages(self) -> None:
        """Restore the last committed issue list after an interrupted progressive read."""
        self.issues = list(self._committed)

    def replace_sources(
        self,
        sources: Sequence[tuple[Sequence[Issue], float]],
        read_at: float,
        loaded_at: datetime | None,
    ) -> None:
        """Apply a read started at `read_at` whose sources were requested at their own
        timestamps, settling each source against its own `source_requested_at`."""
        if read_at < self.requested_at:
            return
        by_key: dict[str, Issue] = {}
        for source_issues, source_requested_at in sources:
            for issue in source_issues:
                by_key[issue.key] = self._settle(issue, source_requested_at)
        self.issues = list(by_key.values())
        self._committed = list(self.issues)
        self.requested_at = read_at
        self.loaded_at = loaded_at
        self._save()

    def replace(
        self, issues: Sequence[Issue], requested_at: float, loaded_at: datetime | None
    ) -> None:
        """Apply a read requested at `requested_at` (on `now`'s clock) and answered at
        `loaded_at`.

        A read requested before the one already applied is ignored. Within a read, an
        issue moved since the read was requested keeps its confirmed status, and one
        written since keeps the copy GitHub confirmed for the write (ADR 0003).
        """
        self.replace_sources([(issues, requested_at)], requested_at, loaded_at)

    def apply_moves(self) -> None:
        """Show the moves confirmed since the loaded read was requested."""
        self.issues = [self._settle(issue, self.requested_at) for issue in self.issues]
        self._committed = [self._settle(issue, self.requested_at) for issue in self._committed]
        self._save()

    def update(self, issue: Issue, written_at: float) -> bool:
        """Replace the loaded copy of `issue` with the one GitHub confirmed for a write
        sent at `written_at` (on `now`'s clock); False when `issue` isn't loaded here."""
        existing = self._written.get(issue.key)
        if existing is not None and written_at < existing[1]:
            return any(loaded.key == issue.key for loaded in self.issues)
        self._written[issue.key] = (issue, written_at)
        applied = self.moves.settle(issue, written_at)
        in_committed = any(loaded.key == issue.key for loaded in self._committed)
        if in_committed:
            self._committed = [
                applied if loaded.key == issue.key else loaded for loaded in self._committed
            ]
        in_issues = any(loaded.key == issue.key for loaded in self.issues)
        if in_issues:
            self.issues = [applied if loaded.key == issue.key else loaded for loaded in self.issues]
        if in_committed or in_issues:
            self._save()
        return in_issues

    def _settle(self, issue: Issue, requested_at: float) -> Issue:
        """`issue` as read at `requested_at`, unless it was written since: then the written
        copy, with only the moves confirmed after the write applied to it."""
        recorded = self._written.get(issue.key)
        if recorded is not None and recorded[1] > requested_at:
            issue, effective_at = recorded
        else:
            effective_at = requested_at
        return self.moves.settle(issue, effective_at)

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
            "issues": [asdict(i) for i in self._committed],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(snapshot), encoding="utf-8")
        except OSError:
            pass  # the snapshot only speeds up the next start; this session is unaffected
