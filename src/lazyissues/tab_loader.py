"""Loading a list tab from `RepoSync` or GitHub search, with progressive page updates."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Protocol

from lazyissues import search
from lazyissues import store as store_module
from lazyissues.config import Config
from lazyissues.github import Gateway
from lazyissues.models import Issue, IssuePage
from lazyissues.repo_sync import RepoSync, RepoSyncUpdate
from lazyissues.store import IssueStore


class TabLoadTarget(Protocol):
    github: Gateway
    store: IssueStore
    NEEDS_VIEWER: ClassVar[bool]

    async def prepare_sync(self, *, force: bool) -> None: ...

    async def queries(self) -> list[str]: ...

    def derive_open(self, issues: Sequence[Issue], viewer: str | None) -> list[Issue]: ...

    def record_viewer(self, viewer: str | None) -> None: ...

    def redraw(self) -> None: ...


def extra_queries(
    tab_queries: Sequence[str],
    config: Config,
    oversized: Sequence[str],
    with_done: bool,
) -> list[str]:
    """Search queries needed beyond `RepoSync`'s qualifying open issues."""
    if not oversized and not with_done:
        return []
    since = (
        datetime.now(UTC).date() - timedelta(days=config.done_window_days) if with_done else None
    )
    oversized_cf = {repo.casefold() for repo in oversized}
    synced = [repo for repo in config.repo_names if repo.casefold() not in oversized_cf]
    oversized_list = list(oversized)
    queries: list[str] = []
    if oversized_list:
        for tab_query in tab_queries:
            if search.targets_only_repos(tab_query, synced):
                continue
            for query in search.with_states(tab_query, None):
                queries.append(search.scoped(query, oversized_list))
    if since is not None:
        for tab_query in tab_queries:
            for query in search.closed_only(tab_query, since):
                queries.append(search.scoped(query, config.repo_names))
    return queries


async def load_with_repo_sync(
    tab: TabLoadTarget,
    repo_sync: RepoSync,
    config: Config,
    with_done: bool,
    *,
    force: bool,
) -> None:
    """Refresh `tab` using `repo_sync` for qualifying repos and search for oversized/done."""
    read_at = store_module.now()
    prep_task = asyncio.create_task(tab.prepare_sync(force=force))
    viewer: str | None = None
    final_update: RepoSyncUpdate | None = None
    had_progressive = False
    try:
        async for update in repo_sync.sync_repos(config.repo_names, force=force):
            if update.viewer:
                viewer = update.viewer
                tab.record_viewer(viewer)
            if not update.final:
                if (
                    prep_task.done()
                    and not prep_task.cancelled()
                    and prep_task.exception() is None
                    and (viewer is not None or not tab.NEEDS_VIEWER)
                ):
                    partial = tab.derive_open(update.issues, viewer)
                    if partial and tab.store.merge_page(partial, update.requested_at):
                        had_progressive = True
                        tab.redraw()
            else:
                final_update = update
        await prep_task
    finally:
        if not prep_task.done():
            prep_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await prep_task
        elif not prep_task.cancelled():
            prep_task.exception()

    assert final_update is not None
    oversized = [repo for repo in config.repo_names if repo in final_update.oversized_repos]
    if viewer is None and tab.NEEDS_VIEWER and (final_update.issues or not oversized):
        viewer, _ = await tab.github.whoami()
        tab.record_viewer(viewer)

    synced_open = tab.derive_open(final_update.issues, viewer)
    tab_queries = await tab.queries() if (oversized or with_done) else []
    extra = extra_queries(tab_queries, config, oversized, with_done)
    if not extra:
        tab.store.replace_sources(
            [(synced_open, final_update.requested_at)],
            read_at,
            loaded_at=final_update.loaded_at,
        )
        return

    all_oversized = len(oversized) == len(config.repo_names)
    await stream_searches(
        tab,
        extra,
        read_at=read_at,
        base_issues=() if all_oversized else synced_open,
        base_requested_at=None if all_oversized else final_update.requested_at,
        base_loaded_at=None if all_oversized else final_update.loaded_at,
        emit_base=bool(not all_oversized and synced_open and not had_progressive),
    )


async def load_with_search(tab: TabLoadTarget, config: Config, with_done: bool) -> None:
    """Refresh `tab` directly from GitHub issue search."""
    read_at = store_module.now()
    since = (
        datetime.now(UTC).date() - timedelta(days=config.done_window_days) if with_done else None
    )
    queries = [
        search.scoped(query, config.repo_names)
        for tab_query in await tab.queries()
        for query in search.with_states(tab_query, since)
    ]
    await stream_searches(tab, queries, read_at=read_at)


async def stream_searches(
    tab: TabLoadTarget,
    queries: Sequence[str],
    *,
    read_at: float | None = None,
    base_issues: Sequence[Issue] = (),
    base_requested_at: float | None = None,
    base_loaded_at: datetime | None = None,
    emit_base: bool = False,
) -> None:
    """Stream `queries` in parallel, progressively merging pages, then commit all sources."""
    search_requested_at = store_module.now()
    effective_read_at = search_requested_at if read_at is None else read_at
    if not queries:
        sources: list[tuple[Sequence[Issue], float]] = []
        if base_requested_at is not None:
            sources.append((base_issues, base_requested_at))
        loaded_at = (
            min(base_loaded_at, datetime.now(UTC))
            if base_loaded_at is not None
            else datetime.now(UTC)
        )
        tab.store.replace_sources(sources, effective_read_at, loaded_at=loaded_at)
        return

    if (
        emit_base
        and base_issues
        and base_requested_at is not None
        and tab.store.merge_page(base_issues, base_requested_at)
    ):
        tab.redraw()

    queue: asyncio.Queue[tuple[IssuePage | None, BaseException | None]] = asyncio.Queue()

    async def pump(query: str) -> None:
        try:
            async with contextlib.aclosing(tab.github.search_issues_pages(query)) as stream:
                async for page in stream:
                    queue.put_nowait((page, None))
            queue.put_nowait((None, None))
        except BaseException as exc:
            queue.put_nowait((None, exc))
            if not isinstance(exc, Exception):
                raise

    tasks = [asyncio.create_task(pump(query)) for query in queries]
    remaining = len(tasks)
    search_found: list[Issue] = []
    try:
        while remaining:
            page, exc = await queue.get()
            if exc is not None:
                raise exc
            if page is None:
                remaining -= 1
                continue
            if page.viewer:
                tab.record_viewer(page.viewer)
            search_found.extend(page.issues)
            more_coming = page.has_next_page or remaining > 1
            if (
                more_coming
                and page.issues
                and tab.store.merge_page(page.issues, search_requested_at)
            ):
                tab.redraw()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    now_utc = datetime.now(UTC)
    if base_requested_at is None:
        sources = [(search_found, search_requested_at)]
        loaded_at = now_utc
    else:
        sources = [(base_issues, base_requested_at), (search_found, search_requested_at)]
        loaded_at = min(base_loaded_at, now_utc) if base_loaded_at is not None else now_utc
    tab.store.replace_sources(sources, effective_read_at, loaded_at=loaded_at)
