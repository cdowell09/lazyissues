"""Shared per-repository open-issue sync, cached and single-flight across tabs."""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from lazyissues import store
from lazyissues.github import Gateway
from lazyissues.models import Issue
from lazyissues.move_tracker import MoveTracker

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


def _apply_write(issues: tuple[Issue, ...], issue: Issue) -> tuple[Issue, ...]:
    if issue.closed:
        return tuple(item for item in issues if item.key != issue.key)
    for index, existing in enumerate(issues):
        if existing.key == issue.key:
            return (*issues[:index], issue, *issues[index + 1 :])
    return (*issues, issue)


class RepoSync:
    """Caches and streams open issues per repo for repos with at most `REPO_SYNC_MAX_OPEN`
    open issues.

    Repos with more than `REPO_SYNC_MAX_OPEN` open issues stop after Page 1, are marked
    oversized, and are reported in `RepoSyncUpdate.oversized_repos` so callers can fall back
    to search. Repos whose stream exceeds `REPO_SYNC_HARD_CAP` mid-sync also fall back.
    """

    def __init__(
        self,
        github: Gateway,
        moves: MoveTracker | None = None,
        *,
        now: Callable[[], float] = store.now,
    ) -> None:
        self._github = github
        self._moves = moves or MoveTracker()
        self._now = now
        self._entries: dict[str, _RepoEntry] = {}
        self._in_flight: dict[str, _InFlightRepo] = {}
        self._writes: dict[str, tuple[Issue, float]] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def close(self) -> None:
        """Cancel any in-flight repository sync tasks."""
        for task in list(self._tasks):
            task.cancel()

    def record_write(self, issue: Issue, written_at: float) -> None:
        """Overlay a confirmed write onto the repo's cached open issues, if not stale."""
        existing_write = self._writes.get(issue.key)
        if existing_write is not None and written_at < existing_write[1]:
            return
        self._writes[issue.key] = (issue, written_at)
        repo_key = issue.repo.casefold()
        entry = self._entries.get(repo_key)
        if entry is None or entry.oversized or written_at < entry.requested_at:
            return
        applied = self._moves.settle(issue, written_at)
        self._entries[repo_key] = replace(entry, issues=_apply_write(entry.issues, applied))

    def apply_moves(self, confirmed: Sequence[tuple[Issue, float]] = ()) -> None:
        """Re-settle cached open issues against confirmed moves, dropping closed ones
        and inserting newly reopened ones."""
        for repo_key, entry in list(self._entries.items()):
            if entry.oversized:
                continue
            settled = tuple(
                updated
                for issue in entry.issues
                if not (updated := self._settle_issue(issue, entry.requested_at)).closed
            )
            if settled != entry.issues:
                self._entries[repo_key] = replace(entry, issues=settled)
        for moved, confirmed_at in confirmed:
            if moved.closed:
                continue
            repo_key = moved.repo.casefold()
            entry = self._entries.get(repo_key)
            if entry is None or entry.oversized or confirmed_at < entry.requested_at:
                continue
            if any(existing.key == moved.key for existing in entry.issues):
                continue
            reopened = self._settle_issue(moved, confirmed_at)
            if not reopened.closed:
                self._entries[repo_key] = replace(entry, issues=(*entry.issues, reopened))

    async def sync_repos(
        self, repos: Sequence[str], *, force: bool = False
    ) -> AsyncIterator[RepoSyncUpdate]:
        """Stream progressive updates and one final snapshot for `repos`."""
        requested_names: dict[str, str] = {}
        for repo in repos:
            requested_names.setdefault(repo.casefold(), repo)

        if not requested_names:
            yield RepoSyncUpdate(
                issues=(),
                requested_at=self._now(),
                loaded_at=datetime.now(UTC),
                final=True,
            )
            return

        entries: dict[str, _RepoEntry] = {}
        pending: dict[str, _InFlightRepo] = {}
        queue: asyncio.Queue[_RepoEvent] = asyncio.Queue()
        viewer: str | None = None

        for key, repo in requested_names.items():
            if not force and (cached := self._entries.get(key)) is not None:
                entries[key] = cached
                viewer = viewer or cached.viewer
            else:
                in_flight = self._start_or_join(repo, key, force=force)
                pending[key] = in_flight
                in_flight.subscribe(queue)

        def current_requested_at() -> float:
            stamps = [e.requested_at for e in entries.values() if not e.oversized] + [
                flight.requested_at for flight in pending.values()
            ]
            return min(stamps) if stamps else min(e.requested_at for e in entries.values())

        def partial(issues: tuple[Issue, ...]) -> RepoSyncUpdate:
            return RepoSyncUpdate(
                issues=issues,
                requested_at=current_requested_at(),
                loaded_at=None,
                final=False,
                viewer=viewer,
            )

        try:
            if pending and entries:
                cached_issues = tuple(
                    issue
                    for key in requested_names
                    if key in entries and not entries[key].oversized
                    for issue in entries[key].issues
                )
                if cached_issues:
                    yield partial(cached_issues)

            while pending:
                event = await queue.get()
                if isinstance(event, _PageEvent):
                    viewer = viewer or event.viewer
                    if event.issues:
                        yield partial(event.issues)
                else:
                    if (done_flight := pending.pop(event.repo_key, None)) is not None:
                        done_flight.unsubscribe(queue)
                    if event.error is not None:
                        raise event.error
                    assert event.entry is not None
                    entries[event.repo_key] = event.entry
                    viewer = viewer or event.entry.viewer
                    if pending and event.last_page and not event.entry.oversized:
                        yield partial(event.last_page)
        finally:
            for in_flight in pending.values():
                in_flight.unsubscribe(queue)

        for key in requested_names:
            entry = entries[key]
            latest = self._entries.get(key)
            if (
                latest is not None
                and latest.requested_at >= entry.requested_at
                and latest.oversized == entry.oversized
            ):
                entries[key] = latest

        all_issues = tuple(
            issue
            for key in requested_names
            if not entries[key].oversized
            for issue in entries[key].issues
        )
        qualifying = [entries[key] for key in requested_names if not entries[key].oversized]
        source_entries = qualifying or list(entries.values())
        oversized = frozenset(
            repo for key, repo in requested_names.items() if entries[key].oversized
        )
        yield RepoSyncUpdate(
            issues=all_issues,
            requested_at=min(entry.requested_at for entry in source_entries),
            loaded_at=min(entry.loaded_at for entry in source_entries),
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

    def _settle_issue(self, issue: Issue, requested_at: float) -> Issue:
        recorded = self._writes.get(issue.key)
        if recorded is not None and recorded[1] > requested_at:
            issue, effective_at = recorded
        else:
            effective_at = requested_at
        return self._moves.settle(issue, effective_at)

    def _settle_repo_issues(
        self, repo_key: str, issues: Sequence[Issue], requested_at: float
    ) -> tuple[Issue, ...]:
        by_key: dict[str, Issue] = {}
        for raw in issues:
            settled = self._settle_issue(raw, requested_at)
            by_key[settled.key] = settled
        for key, (written, written_at) in self._writes.items():
            if (
                key not in by_key
                and written.repo.casefold() == repo_key
                and written_at > requested_at
            ):
                by_key[key] = self._moves.settle(written, written_at)
        return tuple(issue for issue in by_key.values() if not issue.closed)

    async def _run_repo(self, repo: str, repo_key: str, in_flight: _InFlightRepo) -> None:
        try:
            collected: list[Issue] = []
            last_page: tuple[Issue, ...] = ()
            viewer: str | None = None
            fetched = 0
            first_page = True
            async with contextlib.aclosing(
                self._github.repo_open_issues_pages(repo)
            ) as page_stream:
                async for page in page_stream:
                    if first_page:
                        viewer = page.viewer
                        if (page.total_count or 0) > REPO_SYNC_MAX_OPEN:
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
                    fetched += len(page.issues)
                    if fetched > REPO_SYNC_HARD_CAP or (
                        fetched == REPO_SYNC_HARD_CAP and page.has_next_page
                    ):
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
                    settled_page = tuple(
                        settled
                        for raw in page.issues
                        if not (settled := self._settle_issue(raw, in_flight.requested_at)).closed
                    )
                    collected.extend(settled_page)
                    last_page = settled_page
                    if page.has_next_page:
                        in_flight.emit_page(settled_page, viewer if first_page else None)
                    first_page = False

            issues = self._settle_repo_issues(repo_key, collected, in_flight.requested_at)
            entry = _RepoEntry(
                issues=issues,
                requested_at=in_flight.requested_at,
                loaded_at=datetime.now(UTC),
                oversized=False,
                viewer=viewer,
            )
            self._commit(repo_key, entry)
            in_flight.finish(entry, last_page, None)
        except BaseException as exc:
            in_flight.finish(None, (), exc)
            if not isinstance(exc, Exception):
                raise
        finally:
            if self._in_flight.get(repo_key) is in_flight:
                del self._in_flight[repo_key]
