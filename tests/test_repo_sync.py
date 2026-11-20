import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

import pytest

from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import CloseReason, Issue, IssuePage
from lazyissues.move_planner import AddLabel, Close, RemoveLabels, Reopen
from lazyissues.move_tracker import MoveTracker
from lazyissues.repo_sync import REPO_SYNC_HARD_CAP, REPO_SYNC_MAX_OPEN, RepoSync, RepoSyncUpdate


def make_issues(repo: str, count: int) -> list[Issue]:
    return [
        Issue(
            repo=repo,
            number=n,
            title=f"Issue {n}",
            url=f"https://github.com/{repo}/issues/{n}",
            updated_at=f"2026-09-01T{n // 3600:02}:{(n // 60) % 60:02}:{n % 60:02}Z",
        )
        for n in range(1, count + 1)
    ]


@dataclass
class CountingFakeGitHub(FakeGitHub):
    pages_fetched: dict[str, int] = field(default_factory=dict)

    async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
        async for page in super().repo_open_issues_pages(repo):
            self.pages_fetched[repo] = self.pages_fetched.get(repo, 0) + 1
            yield page


async def collect(sync: RepoSync, repos: list[str], *, force: bool = False) -> list[RepoSyncUpdate]:
    return [update async for update in sync.sync_repos(repos, force=force)]


async def test_exact_boundary_300_open_syncs_all_three_pages():
    github = CountingFakeGitHub(
        viewer="me", issues=make_issues("o/r", REPO_SYNC_MAX_OPEN), page_size=100
    )
    sync = RepoSync(github)

    updates = await collect(sync, ["o/r"])

    assert github.pages_fetched["o/r"] == 3
    final = updates[-1]
    assert final.final is True
    assert final.oversized_repos == frozenset()
    assert final.viewer == "me"
    assert len(final.issues) == 300
    assert {issue.number for issue in final.issues} == set(range(1, 301))


async def test_exact_boundary_301_open_stops_after_page_one_and_marks_oversized():
    github = CountingFakeGitHub(
        viewer="me", issues=make_issues("o/r", REPO_SYNC_MAX_OPEN + 1), page_size=100
    )
    sync = RepoSync(github)

    updates = await collect(sync, ["o/r"])

    assert github.pages_fetched["o/r"] == 1
    assert len(updates) == 1
    assert updates[0].final is True
    assert updates[0].issues == ()
    assert updates[0].oversized_repos == frozenset({"o/r"})
    assert updates[0].viewer == "me"

    cached_updates = await collect(sync, ["o/r"])
    assert github.pages_fetched["o/r"] == 1
    assert cached_updates[-1].oversized_repos == frozenset({"o/r"})


async def test_hard_cap_400_issues_without_next_page_stays_synced():
    pages_pulled = 0

    class Exact400GitHub(FakeGitHub):
        async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
            nonlocal pages_pulled
            self.synced_repos.append(repo)
            all_issues = make_issues(repo, REPO_SYNC_HARD_CAP)
            for start in range(0, len(all_issues), 100):
                pages_pulled += 1
                chunk = tuple(all_issues[start : start + 100])
                yield IssuePage(
                    issues=chunk,
                    total_count=REPO_SYNC_MAX_OPEN,
                    has_next_page=start + 100 < len(all_issues),
                    viewer=self.viewer if start == 0 else None,
                )

    sync = RepoSync(Exact400GitHub(viewer="me"))
    updates = await collect(sync, ["o/r"])

    assert pages_pulled == 4
    final = updates[-1]
    assert final.oversized_repos == frozenset()
    assert [issue.number for issue in final.issues] == list(range(1, REPO_SYNC_HARD_CAP + 1))


async def test_hard_cap_400_with_has_next_page_marks_oversized_without_fetching_page_5():
    pages_pulled = 0

    class Growing401GitHub(FakeGitHub):
        async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
            nonlocal pages_pulled
            self.synced_repos.append(repo)
            all_issues = make_issues(repo, REPO_SYNC_HARD_CAP + 1)
            for start in range(0, len(all_issues), 100):
                pages_pulled += 1
                chunk = tuple(all_issues[start : start + 100])
                yield IssuePage(
                    issues=chunk,
                    total_count=REPO_SYNC_MAX_OPEN,
                    has_next_page=start + 100 < len(all_issues),
                    viewer=self.viewer if start == 0 else None,
                )

    sync = RepoSync(Growing401GitHub(viewer="me"))
    updates = await collect(sync, ["o/r"])

    assert pages_pulled == 4
    final = updates[-1]
    assert final.final is True
    assert final.oversized_repos == frozenset({"o/r"})
    assert final.issues == ()


async def test_hard_cap_mid_page_crossing_marks_oversized():
    class MidPageCrossingGitHub(FakeGitHub):
        async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
            self.synced_repos.append(repo)
            all_issues = make_issues(repo, 450)
            yield IssuePage(
                issues=tuple(all_issues[:350]),
                total_count=REPO_SYNC_MAX_OPEN,
                has_next_page=True,
                viewer=self.viewer,
            )
            yield IssuePage(
                issues=tuple(all_issues[350:]),
                total_count=REPO_SYNC_MAX_OPEN,
                has_next_page=False,
                viewer=None,
            )

    sync = RepoSync(MidPageCrossingGitHub(viewer="me"))
    updates = await collect(sync, ["o/r"])

    final = updates[-1]
    assert final.final is True
    assert final.oversized_repos == frozenset({"o/r"})
    assert final.issues == ()


async def test_mixed_multi_repo_returns_qualifying_issues_and_oversized_with_zero_extra_calls():
    github = CountingFakeGitHub(
        viewer="me",
        issues=make_issues("o/a", 50) + make_issues("o/b", REPO_SYNC_MAX_OPEN + 1),
        page_size=100,
    )
    sync = RepoSync(github)

    first = await collect(sync, ["o/a", "o/b"])

    assert len(first[-1].issues) == 50
    assert {issue.repo for issue in first[-1].issues} == {"o/a"}
    assert first[-1].oversized_repos == frozenset({"o/b"})
    assert github.pages_fetched == {"o/a": 1, "o/b": 1}

    second = await collect(sync, ["o/a", "o/b"])
    assert len(second) == 1
    assert len(second[0].issues) == 50
    assert second[0].oversized_repos == frozenset({"o/b"})
    assert github.synced_repos == ["o/a", "o/b"]


async def test_single_flight_coalesces_concurrent_callers_with_progressive_updates():
    page1_sent = asyncio.Event()
    release_page2 = asyncio.Event()

    class GatedFakeGitHub(FakeGitHub):
        async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
            first = True
            async for page in super().repo_open_issues_pages(repo):
                yield page
                if first:
                    first = False
                    page1_sent.set()
                    await release_page2.wait()

    github = GatedFakeGitHub(viewer="me", issues=make_issues("o/r", 4), page_size=2)
    sync = RepoSync(github)

    caller1 = asyncio.create_task(collect(sync, ["o/r"]))
    await page1_sent.wait()
    # Caller 2 joins after Page 1 already arrived; it must still receive Page 1 + final.
    caller2 = asyncio.create_task(collect(sync, ["o/r"]))
    await asyncio.sleep(0)
    release_page2.set()

    updates1, updates2 = await asyncio.gather(caller1, caller2)

    assert github.synced_repos == ["o/r"]
    for updates in (updates1, updates2):
        assert [u.final for u in updates] == [False, True]
        assert len(updates[0].issues) == 2
        assert len(updates[1].issues) == 4
        assert {issue.number for issue in updates[1].issues} == {1, 2, 3, 4}


async def test_progressive_emission_yields_partial_updates_before_final():
    github = FakeGitHub(viewer="me", issues=make_issues("o/r", 5), page_size=2)
    sync = RepoSync(github)

    updates = await collect(sync, ["o/r"])

    assert [u.final for u in updates] == [False, False, True]
    assert [u.loaded_at is None for u in updates] == [True, True, False]
    assert [len(u.issues) for u in updates] == [2, 2, 5]
    assert {issue.number for issue in updates[-1].issues} == {1, 2, 3, 4, 5}


async def test_mid_stream_error_does_not_cache_partial_entry():
    class FlakyFakeGitHub(FakeGitHub):
        fail_page_two: bool = True

        async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
            seen = 0
            async for page in super().repo_open_issues_pages(repo):
                seen += 1
                if seen == 2 and self.fail_page_two:
                    raise GitHubError("page 2 failed")
                yield page

    github = FlakyFakeGitHub(viewer="me", issues=make_issues("o/r", 4), page_size=2)
    sync = RepoSync(github)

    seen_updates: list[RepoSyncUpdate] = []
    with pytest.raises(GitHubError, match="page 2 failed"):
        async for update in sync.sync_repos(["o/r"]):
            seen_updates.append(update)

    assert [u.final for u in seen_updates] == [False]
    assert len(seen_updates[0].issues) == 2

    github.fail_page_two = False
    recovered = await collect(sync, ["o/r"])

    assert len(github.synced_repos) == 2
    assert recovered[-1].final is True
    assert {issue.number for issue in recovered[-1].issues} == {1, 2, 3, 4}


async def test_cancelled_repo_task_does_not_deadlock_subscribers():
    page1_sent = asyncio.Event()
    never = asyncio.Event()

    class HangingGitHub(FakeGitHub):
        async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
            async for page in super().repo_open_issues_pages(repo):
                yield page
                page1_sent.set()
                await never.wait()

    github = HangingGitHub(viewer="me", issues=make_issues("o/r", 4), page_size=2)
    sync = RepoSync(github)

    subscriber = asyncio.create_task(collect(sync, ["o/r"]))
    await page1_sent.wait()
    sync.close()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(subscriber, timeout=1.0)


async def test_record_write_updates_cached_open_drops_closed_and_force_refetches():
    clock = [10.0]
    github = FakeGitHub(viewer="me", issues=make_issues("o/r", 2))
    sync = RepoSync(github, now=lambda: clock[0])

    first = await collect(sync, ["o/r"])
    assert {issue.number for issue in first[-1].issues} == {1, 2}

    updated_1 = Issue("o/r", 1, "Renamed 1", "https://github.com/o/r/issues/1")
    closed_2 = Issue("o/r", 2, "Issue 2", "https://github.com/o/r/issues/2", closed=True)
    created_3 = Issue("o/r", 3, "Created 3", "https://github.com/o/r/issues/3")
    stale_1 = Issue("o/r", 1, "Stale 1", "https://github.com/o/r/issues/1")

    sync.record_write(updated_1, written_at=11.0)
    sync.record_write(closed_2, written_at=12.0)
    sync.record_write(created_3, written_at=13.0)
    sync.record_write(created_3, written_at=13.0)  # idempotent repeat
    sync.record_write(stale_1, written_at=9.0)  # older than requested_at=10.0

    cached = await collect(sync, ["o/r"])
    assert len(github.synced_repos) == 1
    assert [(i.number, i.title) for i in cached[-1].issues] == [(1, "Renamed 1"), (3, "Created 3")]

    clock[0] = 20.0
    forced = await collect(sync, ["o/r"], force=True)
    assert len(github.synced_repos) == 2
    assert sorted((i.number, i.title) for i in forced[-1].issues) == [
        (1, "Issue 1"),
        (2, "Issue 2"),
    ]


async def test_apply_moves_preserves_writes_ignores_rejections_drops_closed_and_adds_reopened():
    clock = [10.0]
    issue1 = Issue("o/r", 1, "Issue 1", "https://github.com/o/r/issues/1", labels=("todo",))
    issue2 = Issue("o/r", 2, "Issue 2", "https://github.com/o/r/issues/2", labels=("todo",))
    issue3 = Issue(
        "o/r", 3, "Issue 3", "https://github.com/o/r/issues/3", labels=("todo",), closed=True
    )
    github = FakeGitHub(viewer="me", issues=[issue1, issue2, issue3])
    moves = MoveTracker()
    sync = RepoSync(github, moves, now=lambda: clock[0])

    initial = await collect(sync, ["o/r"])
    assert [i.number for i in initial[-1].issues] == [1, 2]

    # Rejected move must not change cached issues.
    moves.start(issue1, "doing")
    moves.reject(issue1.key, "forbidden")
    sync.apply_moves()
    cached = await collect(sync, ["o/r"])
    assert cached[-1].issues[0].labels == ("todo",)

    # Concurrent Writer edit (t=11) + confirmed move (t=12) keeps both title and new status.
    edited1 = Issue("o/r", 1, "Edited 1", "https://github.com/o/r/issues/1", labels=("todo",))
    sync.record_write(edited1, written_at=11.0)
    moves.start(issue1, "doing")
    plan1 = [AddLabel("doing"), RemoveLabels(("todo",))]
    moves.confirm(issue1.key, plan1, at=12.0)
    moved1 = Issue("o/r", 1, "Issue 1", "https://github.com/o/r/issues/1", labels=("doing",))
    sync.apply_moves([(moved1, 12.0)])
    cached = await collect(sync, ["o/r"])
    assert [(i.number, i.title, i.labels) for i in cached[-1].issues] == [
        (1, "Edited 1", ("doing",)),
        (2, "Issue 2", ("todo",)),
    ]

    # Confirmed close removes issue #2 from cached open issues.
    moves.start(issue2, "Close as completed")
    moves.confirm(issue2.key, [Close(CloseReason.COMPLETED)], at=13.0)
    closed2 = Issue("o/r", 2, "Issue 2", "https://github.com/o/r/issues/2", closed=True)
    sync.apply_moves([(closed2, 13.0)])
    cached = await collect(sync, ["o/r"])
    assert [i.number for i in cached[-1].issues] == [1]

    # Confirmed reopen inserts issue #3 into cached open issues.
    moves.start(issue3, "Reopen")
    moves.confirm(issue3.key, [Reopen()], at=14.0)
    reopened3 = Issue("o/r", 3, "Issue 3", "https://github.com/o/r/issues/3", labels=("todo",))
    sync.apply_moves([(reopened3, 14.0)])
    cached = await collect(sync, ["o/r"])
    assert [i.number for i in cached[-1].issues] == [1, 3]
