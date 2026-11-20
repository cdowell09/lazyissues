"""Startup syncs configured repos once, preloads inactive tabs, and defers drawing hidden tabs."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from listed import plain, show_tab
from textual.widgets import DataTable, TabbedContent

from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.config import Config, Repo, Status
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import Issue, IssuePage, Milestone
from lazyissues.repo_sync import REPO_SYNC_MAX_OPEN
from lazyissues.views.issue_list import IssueList, IssueTable
from lazyissues.views.milestones import Milestones
from lazyissues.views.my_work import MyWork
from lazyissues.views.team import Team
from lazyissues.views.unassigned import Unassigned


async def settle(pilot) -> None:
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def until(pilot, condition: Callable[[], bool]) -> None:
    for _ in range(200):
        if condition():
            return
        await pilot.pause(0.01)
    raise AssertionError("timed out")


def _build_repo_issues(count: int) -> list[Issue]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    issues: list[Issue] = []
    for number in range(1, count + 1):
        if number == 1:
            assignees, milestone = ("me",), "v1"
        elif number == 2:
            assignees, milestone = ("sam",), "v1"
        elif number == 3:
            assignees, milestone = (), None
        else:
            assignees, milestone = ("other",), None
        issues.append(
            Issue(
                "o/r",
                number,
                f"Issue {number}",
                f"https://github.com/o/r/issues/{number}",
                assignees=assignees,
                labels=("todo",),
                milestone=milestone,
                updated_at=(base + timedelta(minutes=number)).isoformat(),
            )
        )
    return issues


class TrackingGitHub(FakeGitHub):
    """Counts `whoami()` calls on top of `FakeGitHub`."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.whoami_calls = 0

    async def whoami(self) -> tuple[str, set[str]]:
        self.whoami_calls += 1
        return await super().whoami()


class GatedSync(FakeGitHub):
    """Holds `repo_open_issues_pages` until `gate` is opened."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.gate = asyncio.Event()

    async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
        await self.gate.wait()
        async for page in super().repo_open_issues_pages(repo):
            yield page


class Page2Gated(FakeGitHub):
    """Yields Page 1 of `repo_open_issues_pages` immediately, then holds Page 2 until `page2`."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.page1_sent = asyncio.Event()
        self.page2 = asyncio.Event()

    async def repo_open_issues_pages(self, repo: str) -> AsyncGenerator[IssuePage, None]:
        async for index, page in _enumerate_async(super().repo_open_issues_pages(repo)):
            if index == 0:
                yield page
                self.page1_sent.set()
            else:
                await self.page2.wait()
                yield page


async def _enumerate_async(
    stream: AsyncIterator[IssuePage],
) -> AsyncIterator[tuple[int, IssuePage]]:
    index = 0
    async for item in stream:
        yield index, item
        index += 1


async def test_startup_syncs_repos_once_and_preloads_inactive_tabs_without_drawing_them():
    config = demo.config()
    github = demo.github()
    app = LazyIssuesApp(config, github)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert github.synced_repos == config.repo_names
        assert github.searched == []

        for view_type in (MyWork, Team, Milestones, Unassigned):
            view = app.query_one(view_type)
            assert view._loaded
            assert view.store.issues

        assert len(app.query_one("#my-work IssueTable", IssueTable).rows) > 0
        for hidden in ("#team", "#milestones", "#unassigned"):
            assert len(app.query_one(f"{hidden} IssueTable", IssueTable).rows) == 0

        tabs = app.query_one(TabbedContent)
        tabs.active = "tab-3"  # Unassigned
        await settle(pilot)
        assert len(app.query_one("#unassigned IssueTable", IssueTable).rows) > 0
        assert github.searched == []
        assert github.synced_repos == config.repo_names

        tabs.active = "tab-0"
        tabs.active = "tab-3"
        await settle(pilot)
        assert github.searched == []
        assert github.synced_repos == config.repo_names


async def test_a_reload_of_a_tab_never_shown_waits_for_its_first_show():
    github = GatedSync(**vars(demo.github()))
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await pilot.pause()
        team = app.query_one(Team)
        assert not team._loaded

        team.reload()  # as a write that regroups does to every tab
        await pilot.pause()
        assert not team._loaded
        assert not team.refreshing
        assert github.searched == []

        github.gate.set()
        await show_tab(pilot, "team")
        assert team._loaded
        assert team.store.issues
        assert github.searched == []


async def test_a_refresh_that_finishes_while_a_tab_is_hidden_redraws_it_when_shown(monkeypatch):
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        tabs = app.query_one(TabbedContent)
        tabs.active = "tab-3"  # Unassigned, loaded now
        await settle(pilot)
        tabs.active = "tab-0"
        await settle(pilot)
        unassigned = app.query_one(Unassigned)
        drawn: list[None] = []
        real_show = unassigned.show

        def counting_show() -> None:
            drawn.append(None)
            real_show()

        monkeypatch.setattr(unassigned, "show", counting_show)

        unassigned.reload()
        await settle(pilot)
        assert drawn == []  # hidden: not drawn

        tabs.active = "tab-3"
        await settle(pilot)
        assert len(drawn) == 1


async def test_applying_a_config_to_a_hidden_tab_redraws_it_only_when_shown(monkeypatch):
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settle(pilot)
        team = app.query_one(Team)  # preloaded while hidden, never shown
        drawn: list[None] = []
        real_show = team.show

        def counting_show() -> None:
            drawn.append(None)
            real_show()

        monkeypatch.setattr(team, "show", counting_show)

        team.configure(app.config)
        await settle(pilot)
        assert drawn == []

        await show_tab(pilot, "team")
        assert len(drawn) == 1


async def test_startup_draws_cached_snapshot_only_for_the_start_tab(tmp_path):
    from lazyissues.store import IssueStore, snapshot_path

    config = demo.config()
    github = demo.github()
    for view_id in ("my-work", "team", "milestones", "unassigned"):
        store = IssueStore(snapshot_path(tmp_path, view_id, config.repo_names))
        store.replace(github.issues, requested_at=0.0, loaded_at=None)
        store.flush()

    app = LazyIssuesApp(config, github, cache=tmp_path)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert len(app.query_one("#my-work IssueTable", IssueTable).rows) > 0
        for hidden in ("#team", "#milestones", "#unassigned"):
            assert len(app.query_one(f"{hidden} IssueTable", IssueTable).rows) == 0

        await show_tab(pilot, "unassigned")
        assert len(app.query_one("#unassigned IssueTable", IssueTable).rows) > 0


async def test_300_open_issues_uses_shared_repo_sync_without_searches():
    config = Config(repos=[Repo("o/r")], statuses=[Status("Todo")], team=["sam"])
    github = TrackingGitHub(
        viewer="me",
        issues=_build_repo_issues(REPO_SYNC_MAX_OPEN),
        milestones={"o/r": ["v1"]},
    )
    app = LazyIssuesApp(config, github)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert github.synced_repos == ["o/r"]
        assert github.searched == []
        assert github.whoami_calls == 0
        assert [i.key for i in app.query_one(MyWork).store.issues] == ["o/r#1"]
        assert {i.key for i in app.query_one(Team).store.issues} == {"o/r#1", "o/r#2"}
        assert {i.key for i in app.query_one(Milestones).store.issues} == {"o/r#1", "o/r#2"}
        assert [i.key for i in app.query_one(Unassigned).store.issues] == ["o/r#3"]
        for hidden in ("#team", "#milestones", "#unassigned"):
            assert len(app.query_one(f"{hidden} IssueTable", IssueTable).rows) == 0


async def test_301_open_issues_falls_back_to_search_batches_viewer_and_preloads_sequentially(
    monkeypatch,
):
    config = Config(repos=[Repo("o/r")], statuses=[Status("Todo")], team=["sam"])
    github = TrackingGitHub(
        viewer="me",
        issues=_build_repo_issues(REPO_SYNC_MAX_OPEN + 1),
        milestones={"o/r": ["v1"]},
    )
    events: list[tuple[str, str]] = []
    active_preloads = 0
    max_concurrent_preloads = 0
    real_preload = IssueList.preload

    async def tracked_preload(self: IssueList) -> None:
        nonlocal active_preloads, max_concurrent_preloads
        if self._loaded:
            await real_preload(self)
            return
        active_preloads += 1
        max_concurrent_preloads = max(max_concurrent_preloads, active_preloads)
        events.append(("start", self.id or ""))
        try:
            await real_preload(self)
        finally:
            events.append(("end", self.id or ""))
            active_preloads -= 1

    monkeypatch.setattr(IssueList, "preload", tracked_preload)

    app = LazyIssuesApp(config, github)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert github.synced_repos == ["o/r"]
        assert github.searched
        assert github.whoami_calls == 0
        assert app.query_one(Team).viewer == "me"
        assert max_concurrent_preloads == 1
        assert events == [
            ("start", "team"),
            ("end", "team"),
            ("start", "milestones"),
            ("end", "milestones"),
            ("start", "unassigned"),
            ("end", "unassigned"),
        ]
        assert [i.key for i in app.query_one(MyWork).store.issues] == ["o/r#1"]
        assert {i.key for i in app.query_one(Team).store.issues} == {"o/r#1", "o/r#2"}
        assert {i.key for i in app.query_one(Milestones).store.issues} == {"o/r#1", "o/r#2"}
        assert [i.key for i in app.query_one(Unassigned).store.issues] == ["o/r#3"]
        for hidden in ("#team", "#milestones", "#unassigned"):
            assert len(app.query_one(f"{hidden} IssueTable", IssueTable).rows) == 0


async def test_progressive_pages_render_before_page_2_and_preserve_adr_0003_moves():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    issues = [
        Issue(
            "o/r",
            number,
            f"Issue {number}",
            f"https://github.com/o/r/issues/{number}",
            assignees=("me",),
            labels=("todo",),
            updated_at=(base + timedelta(minutes=10 - number)).isoformat(),
        )
        for number in range(1, 5)
    ]
    config = Config(
        repos=[Repo("o/r")],
        statuses=[Status("Todo"), Status("In Review", key="v")],
    )
    github = Page2Gated(
        viewer="me",
        issues=issues,
        labels={"o/r": ["todo", "in-review"]},
        page_size=2,
    )
    app = LazyIssuesApp(config, github)

    def listed_keys() -> list[str]:
        table = app.query_one("#my-work DataTable", DataTable)
        return [key for row in table.ordered_rows if (key := row.key.value)]

    def status_groups() -> dict[str, list[str]]:
        table = app.query_one("#my-work DataTable", DataTable)
        grouped: dict[str, list[str]] = {}
        current: list[str] = []
        for row, text in zip(table.ordered_rows, plain(table), strict=True):
            if row.key.value is None:
                current = grouped.setdefault(text.rsplit(" (", 1)[0], [])
            else:
                current.append(row.key.value)
        return grouped

    async with app.run_test() as pilot:
        await github.page1_sent.wait()
        await until(pilot, lambda: listed_keys() == ["o/r#1", "o/r#2"])
        assert not github.page2.is_set()

        table = app.query_one("#my-work DataTable", DataTable)
        table.move_cursor(row=table.get_row_index("o/r#1"))
        await pilot.pause()
        await pilot.press("V")
        await until(pilot, lambda: "o/r#1" in status_groups().get("In Review", []))

        github.page2.set()
        await settle(pilot)
        assert set(listed_keys()) == {"o/r#1", "o/r#2", "o/r#3", "o/r#4"}
        assert status_groups()["In Review"] == ["o/r#1"]
        assert status_groups()["Todo"] == ["o/r#2", "o/r#3", "o/r#4"]


async def test_preload_worker_does_not_cancel_in_flight_mover_worker():
    class Page2AndWriteGated(Page2Gated):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.write_gate = asyncio.Event()

        async def add_labels(self, repo: str, number: int, names: Sequence[str]) -> None:
            await self.write_gate.wait()
            await super().add_labels(repo, number, names)

    base = datetime(2026, 1, 1, tzinfo=UTC)
    issues = [
        Issue(
            "o/r",
            number,
            f"Issue {number}",
            f"https://github.com/o/r/issues/{number}",
            assignees=("me",),
            labels=("todo",),
            updated_at=(base + timedelta(minutes=10 - number)).isoformat(),
        )
        for number in range(1, 5)
    ]
    config = Config(
        repos=[Repo("o/r")],
        statuses=[Status("Todo"), Status("In Review", key="v")],
    )
    github = Page2AndWriteGated(
        viewer="me",
        issues=issues,
        labels={"o/r": ["todo", "in-review"]},
        page_size=2,
    )
    app = LazyIssuesApp(config, github)
    async with app.run_test() as pilot:
        await github.page1_sent.wait()
        table = app.query_one("#my-work DataTable", DataTable)
        await until(pilot, lambda: "o/r#1" in table.rows)
        table.move_cursor(row=table.get_row_index("o/r#1"))
        await pilot.pause()
        await pilot.press("V")
        await until(pilot, lambda: app.moves.pending("o/r#1") == "In Review")

        # Finishing MyWork launches `_preload_tabs` in group="preload"; Mover must stay alive.
        github.page2.set()
        await until(pilot, lambda: app.query_one(Unassigned)._loaded)
        assert app.moves.pending("o/r#1") == "In Review"

        from lazyissues.statuses import StatusRules

        github.write_gate.set()
        await settle(pilot)
        assert app.moves.pending("o/r#1") is None
        [moved] = [i for i in app.query_one(MyWork).store.issues if i.key == "o/r#1"]
        assert StatusRules(config).status_of(moved).name == "In Review"


async def test_failed_silent_preload_retries_on_show_and_reload_during_preload_toasts():
    class FlakyMilestones(FakeGitHub):
        def __post_init__(self) -> None:
            super().__post_init__()
            self.fail_milestones = True
            self.gate: asyncio.Event | None = None

        async def repo_milestones(self, repo: str, fresh: bool = False) -> list[Milestone]:
            if self.gate is not None:
                await self.gate.wait()
            if self.fail_milestones:
                raise GitHubError("milestones down")
            return await super().repo_milestones(repo, fresh=fresh)

    config = Config(repos=[Repo("o/r")], statuses=[Status("Todo")], team=["sam"])
    github = FlakyMilestones(viewer="me", issues=_build_repo_issues(5), milestones={"o/r": ["v1"]})
    app = LazyIssuesApp(config, github)
    async with app.run_test(notifications=True) as pilot:
        await settle(pilot)
        milestones = app.query_one(Milestones)
        assert not milestones._loaded
        assert list(app.screen.query("Toast")) == []

        github.fail_milestones = False
        await show_tab(pilot, "milestones")
        assert milestones._loaded
        assert milestones.error is None
        assert {i.key for i in milestones.store.issues} == {"o/r#1", "o/r#2"}

    # Interactive reload() while silent preload is in flight clears silent and shows error toast.
    gated = FlakyMilestones(viewer="me", issues=_build_repo_issues(5), milestones={"o/r": ["v1"]})
    gated.gate = asyncio.Event()
    app2 = LazyIssuesApp(config, gated)
    async with app2.run_test(notifications=True) as pilot:
        milestones = app2.query_one(Milestones)
        await until(pilot, lambda: milestones.refreshing)
        milestones.reload(force=True)
        gated.gate.set()
        await settle(pilot)
        toasts = [str(toast.render()) for toast in app2.screen.query("Toast")]
        assert any("milestones down" in text for text in toasts)


async def test_oversized_repo_force_false_reload_does_not_revert_external_github_changes():
    config = Config(
        repos=[Repo("o/r")],
        statuses=[Status("Todo"), Status("In Review", key="v"), Status("Blocked")],
    )
    github = FakeGitHub(
        viewer="me",
        issues=_build_repo_issues(REPO_SYNC_MAX_OPEN + 1),
        labels={"o/r": ["todo", "in-review", "blocked"]},
        milestones={"o/r": ["v1"]},
    )
    app = LazyIssuesApp(config, github)
    async with app.run_test() as pilot:
        await settle(pilot)
        table = app.query_one("#my-work DataTable", DataTable)
        table.move_cursor(row=table.get_row_index("o/r#1"))
        await pilot.pause()
        await pilot.press("V")
        await settle(pilot)
        from lazyissues.statuses import StatusRules

        rules = StatusRules(config)
        [moved] = [i for i in app.query_one(MyWork).store.issues if i.key == "o/r#1"]
        assert rules.status_of(moved).name == "In Review"

        # External change on GitHub after our local move.
        for idx, item in enumerate(github.issues):
            if item.key == "o/r#1":
                github.issues[idx] = replace(item, labels=("blocked",))
                break

        # Pressing `d` triggers reload(force=False); fresh search must keep external state.
        await pilot.press("d")
        await settle(pilot)
        [reloaded] = [i for i in app.query_one(MyWork).store.issues if i.key == "o/r#1"]
        assert rules.status_of(reloaded).name == "Blocked"


def test_startup_imports_skip_detail_setup_demo_and_markdown():
    import subprocess
    import sys

    check = (
        "import sys, lazyissues.__main__, lazyissues.app; "
        "loaded = [m for m in ('markdown_it', 'pygments', 'lazyissues.detail', "
        "'lazyissues.setup', 'lazyissues.demo') if m in sys.modules]; "
        "assert not loaded, loaded"
    )
    proc = subprocess.run([sys.executable, "-c", check], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
