"""The loaded issues, persisted as a snapshot so the next start is instant."""

import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import Any

import platformdirs

from lazyissues.models import Issue

SNAPSHOT_VERSION = 2  # bump whenever `Issue` changes shape


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir("lazyissues", appauthor=False))


def snapshot_path(directory: Path, repos: list[str]) -> Path:
    """Where the snapshot for this repo set lives; the repos' order doesn't matter."""
    digest = hashlib.sha256("\n".join(sorted(repos)).encode()).hexdigest()[:16]
    return directory / f"snapshot-{digest}.json"


def _issue(fields: dict[str, Any]) -> Issue:
    # JSON has no tuples; `Issue` keeps its sequences as tuples.
    values: dict[str, Any] = {k: tuple(v) if isinstance(v, list) else v for k, v in fields.items()}
    return Issue(**values)


class IssueStore:
    """The loaded issues, opened from the snapshot at `path` (none: memory only)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.issues: list[Issue] = self._load()
        self.requested_at = -math.inf  # when the read behind `issues` was requested

    def replace(self, issues: list[Issue], requested_at: float) -> bool:
        """Apply a read stamped with when it was requested; false if it was older (ADR 0003)."""
        if requested_at < self.requested_at:
            return False
        self.issues = list(issues)
        self.requested_at = requested_at
        self._save()
        return True

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
