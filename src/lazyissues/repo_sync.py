"""Shared per-repository open-issue sync, cached and single-flight across tabs."""

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from lazyissues import store
from lazyissues.github import Gateway
from lazyissues.models import Issue

REPO_SYNC_MAX_OPEN = 300
REPO_SYNC_HARD_CAP = 400


@dataclass(frozen=True)
class RepoSyncUpdate:
    """One progressive or final update from syncing a set of repositories."""

    issues: tuple[Issue, ...]
    requested_at: float
    loaded_at: datetime | None
    final: bool
    viewer: str | None = None
    oversized_repos: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _RepoEntry:
    issues: tuple[Issue, ...]
    requested_at: float
    loaded_at: datetime
    oversized: bool
    viewer: str | None = None


@dataclass(frozen=True)
class _PageEvent:
    repo_key: str
    issues: tuple[Issue, ...]
    viewer: str | None


@dataclass(frozen=True)
class _DoneEvent:
    repo_key: str
    entry: _RepoEntry | None
    last_page: tuple[Issue, ...]
    error: BaseException | None


_RepoEvent = _PageEvent | _DoneEvent


class _InFlightRepo:
    """Single-flight publisher for one repo's in-progress `repo_open_issues_pages` stream."""

    def __init__(self, repo_key: str, requested_at: float) -> None:
        self.repo_key = repo_key
        self.requested_at = requested_at
        self._pages: list[_PageEvent] = []
        self._done: _DoneEvent | None = None
        self._listeners: list[asyncio.Queue[_RepoEvent]] = []

    def subscribe(self, queue: asyncio.Queue[_RepoEvent]) -> None:
        self._listeners.append(queue)
        for event in self._pages:
            queue.put_nowait(event)
        if self._done is not None:
            queue.put_nowait(self._done)

    def unsubscribe(self, queue: asyncio.Queue[_RepoEvent]) -> None:
        if queue in self._listeners:
            self._listeners.remove(queue)

    def emit_page(self, issues: tuple[Issue, ...], viewer: str | None) -> None:
        event = _PageEvent(self.repo_key, issues, viewer)
        self._pages.append(event)
        for queue in list(self._listeners):
            queue.put_nowait(event)

    def finish(
        self,
        entry: _RepoEntry | None,
        last_page: tuple[Issue, ...],
        error: BaseException | None,
    ) -> None:
        event = _DoneEvent(self.repo_key, entry, last_page, error)
        self._done = event
        for queue in list(self._listeners):
            queue.put_nowait(event)


def _dedupe(issues: Sequence[Issue]) -> tuple[Issue, ...]:
    """Deduplicate by issue key, keeping first-seen order and the latest copy of each key."""
    result: list[Issue] = []
    positions: dict[str, int] = {}
    for issue in issues:
        if (index := positions.get(issue.key)) is not None:
            result[index] = issue
        else:
            positions[issue.key] = len(result)
            result.append(issue)
    return tuple(result)


def _apply_write(issues: tuple[Issue, ...], issue: Issue) -> tuple[Issue, ...]:
    """Overlay one confirmed write onto a repo's open issues; idempotent."""
    if issue.closed:
        return tuple(item for item in issues if item.key != issue.key)
    for index, existing in enumerate(issues):
        if existing.key == issue.key:
            return (*issues[:index], issue, *issues[index + 1 :])
    return (*issues, issue)


class RepoSync:
    """Caches and streams open issues per repo for repos with at most `max_open` open issues.

    Repos with more than `max_open` open issues stop after Page 1, are marked oversized, and
    are reported in `RepoSyncUpdate.oversized_repos` so callers can fall back to search.
    """

    def __init__(
        self,
        github: Gateway,
        *,
        max_open: int = REPO_SYNC_MAX_OPEN,
        hard_cap: int = REPO_SYNC_HARD_CAP,
        now: Callable[[], float] = store.now,
    ) -> None:
        self._github = github
        self._max_open = max_open
        self._hard_cap = hard_cap
        self._now = now
        self._entries: dict[str, _RepoEntry] = {}
        self._in_flight: dict[str, _InFlightRepo] = {}
        self._writes: dict[str, tuple[Issue, float]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def record_write(self, issue: Issue, written_at: float) -> None:
        """Overlay a confirmed write onto the repo's cached open issues, if not stale."""
        existing_write = self._writes.get(issue.key)
        if existing_write is None or written_at >= existing_write[1]:
            self._writes[issue.key] = (issue, written_at)
        repo_key = issue.repo.casefold()
        entry = self._entries.get(repo_key)
        if entry is None or entry.oversized or written_at < entry.requested_at:
            return
        applied = self._writes[issue.key][0]
        self._entries[repo_key] = replace(entry, issues=_apply_write(entry.issues, applied))

    def invalidate(self, repo: str) -> None:
        """Drop cached state for `repo` so the next sync fetches fresh data."""
        repo_key = repo.casefold()
        self._entries.pop(repo_key, None)
        self._in_flight.pop(repo_key, None)

    async def sync_repos(
        self, repos: Sequence[str], *, force: bool = False
    ) -> AsyncIterator[RepoSyncUpdate]:
        """Stream progressive updates and one final snapshot for `repos`."""
        ordered_keys: list[str] = []
        requested_names: dict[str, str] = {}
        for repo in repos:
            key = repo.casefold()
            if key not in requested_names:
                ordered_keys.append(key)
                requested_names[key] = repo

        if not ordered_keys:
            yield RepoSyncUpdate(
                issues=(),
                requested_at=self._now(),
                loaded_at=datetime.now(UTC),
                final=True,
            )
            return

        entries: dict[str, _RepoEntry] = {}
        pending: dict[str, _InFlightRepo] = {}
        subscribed: list[_InFlightRepo] = []
        queue: asyncio.Queue[_RepoEvent] = asyncio.Queue()
        viewer: str | None = None

        for key in ordered_keys:
            if not force and (cached := self._entries.get(key)) is not None:
                entries[key] = cached
                viewer = viewer or cached.viewer
            else:
                in_flight = self._start_or_join(requested_names[key], key, force=force)
                pending[key] = in_flight
                subscribed.append(in_flight)
                in_flight.subscribe(queue)

        def current_requested_at() -> float:
            stamps = [e.requested_at for e in entries.values() if not e.oversized] + [
                flight.requested_at for flight in pending.values()
            ]
            if stamps:
                return min(stamps)
            return min(e.requested_at for e in entries.values())

        def current_oversized() -> frozenset[str]:
            return frozenset(
                requested_names[key]
                for key in ordered_keys
                if key in entries and entries[key].oversized
            )

        try:
            if pending and entries:
                cached_issues = tuple(
                    issue
                    for key in ordered_keys
                    if key in entries and not entries[key].oversized
                    for issue in entries[key].issues
                )
                if cached_issues:
                    yield RepoSyncUpdate(
                        issues=cached_issues,
                        requested_at=current_requested_at(),
                        loaded_at=None,
                        final=False,
                        viewer=viewer,
                        oversized_repos=current_oversized(),
                    )

            while pending:
                event = await queue.get()
                if isinstance(event, _PageEvent):
                    viewer = viewer or event.viewer
                    if event.issues:
                        yield RepoSyncUpdate(
                            issues=event.issues,
                            requested_at=current_requested_at(),
                            loaded_at=None,
                            final=False,
                            viewer=viewer,
                            oversized_repos=current_oversized(),
                        )
                else:
                    pending.pop(event.repo_key, None)
                    if event.error is not None:
                        raise event.error
                    assert event.entry is not None
                    entries[event.repo_key] = event.entry
                    viewer = viewer or event.entry.viewer
                    if pending and event.last_page and not event.entry.oversized:
                        yield RepoSyncUpdate(
                            issues=event.last_page,
                            requested_at=current_requested_at(),
                            loaded_at=None,
                            final=False,
                            viewer=viewer,
                            oversized_repos=current_oversized(),
                        )
        finally:
            for in_flight in subscribed:
                in_flight.unsubscribe(queue)

        final_entries: dict[str, _RepoEntry] = {}
        for key in ordered_keys:
            entry = entries[key]
            latest = self._entries.get(key)
            if (
                latest is not None
                and latest.requested_at >= entry.requested_at
                and latest.oversized == entry.oversized
            ):
                entry = latest
            final_entries[key] = entry

        all_issues = tuple(
            issue
            for key in ordered_keys
            if not final_entries[key].oversized
            for issue in final_entries[key].issues
        )
        qualifying = [
            final_entries[key] for key in ordered_keys if not final_entries[key].oversized
        ]
        source_entries = qualifying or list(final_entries.values())
        loaded_at = min(entry.loaded_at for entry in source_entries)
        requested_at = min(entry.requested_at for entry in source_entries)
        oversized = frozenset(
            requested_names[key] for key in ordered_keys if final_entries[key].oversized
        )
        yield RepoSyncUpdate(
            issues=all_issues,
            requested_at=requested_at,
            loaded_at=loaded_at,
            final=True,
            viewer=viewer,
            oversized_repos=oversized,
        )

    def _start_or_join(self, repo: str, repo_key: str, *, force: bool) -> _InFlightRepo:
        if not force and (existing := self._in_flight.get(repo_key)) is not None:
            return existing
        in_flight = _InFlightRepo(repo_key, self._now())
        self._in_flight[repo_key] = in_flight
        task = asyncio.create_task(self._run_repo(repo, repo_key, in_flight))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return in_flight

    def _commit(self, repo_key: str, entry: _RepoEntry) -> None:
        existing = self._entries.get(repo_key)
        if existing is None or entry.requested_at >= existing.requested_at:
            self._entries[repo_key] = entry

    def _overlay_writes(
        self, repo_key: str, issues: tuple[Issue, ...], requested_at: float
    ) -> tuple[Issue, ...]:
        pending_writes = sorted(
            (
                (written_at, issue)
                for issue, written_at in self._writes.values()
                if issue.repo.casefold() == repo_key and written_at >= requested_at
            ),
            key=lambda pair: pair[0],
        )
        for _, issue in pending_writes:
            issues = _apply_write(issues, issue)
        return issues

    async def _run_repo(self, repo: str, repo_key: str, in_flight: _InFlightRepo) -> None:
        pages: AsyncIterator[object] | None = None
        try:
            page_stream = self._github.repo_open_issues_pages(repo)
            pages = page_stream
            first = await anext(page_stream, None)
            if first is None:
                entry = _RepoEntry(
                    issues=(),
                    requested_at=in_flight.requested_at,
                    loaded_at=datetime.now(UTC),
                    oversized=False,
                )
                self._commit(repo_key, entry)
                in_flight.finish(entry, (), None)
                return

            viewer = first.viewer
            if (first.total_count or 0) > self._max_open:
                entry = _RepoEntry(
                    issues=(),
                    requested_at=in_flight.requested_at,
                    loaded_at=datetime.now(UTC),
                    oversized=True,
                    viewer=viewer,
                )
                self._commit(repo_key, entry)
                in_flight.finish(entry, (), None)
                return

            collected: list[Issue] = list(first.issues[: self._hard_cap])
            last_page = tuple(collected)
            has_more = first.has_next_page and len(collected) < self._hard_cap
            if has_more:
                in_flight.emit_page(last_page, viewer)

            while has_more:
                page = await anext(page_stream, None)
                if page is None:
                    break
                remaining = self._hard_cap - len(collected)
                chunk = page.issues[:remaining]
                collected.extend(chunk)
                last_page = chunk
                has_more = page.has_next_page and len(collected) < self._hard_cap
                if has_more:
                    in_flight.emit_page(chunk, None)

            issues = self._overlay_writes(repo_key, _dedupe(collected), in_flight.requested_at)
            entry = _RepoEntry(
                issues=issues,
                requested_at=in_flight.requested_at,
                loaded_at=datetime.now(UTC),
                oversized=False,
                viewer=viewer,
            )
            self._commit(repo_key, entry)
            in_flight.finish(entry, last_page, None)
        except Exception as exc:
            in_flight.finish(None, (), exc)
        finally:
            if pages is not None:
                aclose = getattr(pages, "aclose", None)
                if aclose is not None:
                    await aclose()
            if self._in_flight.get(repo_key) is in_flight:
                del self._in_flight[repo_key]
