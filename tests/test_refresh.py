"""My Work opens from the snapshot and refreshes in the background."""

import asyncio
from datetime import UTC, datetime, timedelta

from textual.app import App
from textual.pilot import Pilot
from textual.widgets import DataTable, Static

from lazyissues import demo
from lazyissues import store as store_module
from lazyissues.app import LazyIssuesApp
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import Issue, IssueDetail
from lazyissues.store import IssueStore, snapshot_path
from lazyissues.views import issue_list
from lazyissues.views.issue_list import IssueList

MY_DEMO_ISSUES = [  # in display order, by status group
    "octo-dev/lanternfish#9",
    "octo-dev/tidepool#12",
    "octo-dev/lanternfish#4",
    "octo-dev/tidepool#15",
    "octo-dev/lanternfish#11",
]


class Gated(FakeGitHub):
    """A fake GitHub, seeded from `github`, whose searches wait until the test opens the gate."""

    def __init__(self, github: FakeGitHub) -> None:
        super().__init__(**vars(github))
        self.gate = asyncio.Event()

    async def search_issues(self, query: str) -> list[Issue]:
        await self.gate.wait()
        return await super().search_issues(query)


class Unreachable(FakeGitHub):
    async def search_issues(self, query: str) -> list[Issue]:
        raise GitHubError("Couldn't reach GitHub: timed out")

    async def issue_detail(self, repo: str, number: int) -> IssueDetail:
        raise GitHubError("Couldn't reach GitHub: timed out")


def table(app: App) -> DataTable:
    return app.query_one("#my-work DataTable", DataTable)


def listed(app: LazyIssuesApp) -> list[str]:
    """The keys of the listed issues, leaving out group headers."""
    return [key for row in table(app).ordered_rows if (key := row.key.value)]


def selected(app: App) -> str | None:
    t = table(app)
    return t.coordinate_to_cell_key(t.cursor_coordinate).row_key.value


async def select(pilot: Pilot, key: str) -> None:
    """Move the cursor down to the issue `key`, as a user would."""
    for _ in range(table(pilot.app).row_count):
        if selected(pilot.app) == key:
            return
        await pilot.press("down")
    raise AssertionError(f"{key} isn't listed")


async def refreshed(pilot: Pilot) -> None:
    await pilot.press("r")
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def refreshing(app: LazyIssuesApp) -> bool:
    return app.query_one("#my-work #refreshing").display


def notifications(app: LazyIssuesApp) -> list[str]:
    return [str(toast.render()) for toast in app.screen.query("Toast")]


async def test_a_second_launch_shows_the_snapshot_before_github_answers(tmp_path):
    first = LazyIssuesApp(demo.config(), demo.github(), tmp_path)
    async with first.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()

    app = LazyIssuesApp(demo.config(), Gated(demo.github()), tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert listed(app) == MY_DEMO_ISSUES


async def test_an_indicator_shows_while_refreshing():
    github = Gated(demo.github())
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert refreshing(app)

        github.gate.set()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert not refreshing(app)
        assert listed(app) == MY_DEMO_ISSUES


async def test_a_failed_refresh_keeps_the_loaded_issues_and_shows_the_error(tmp_path):
    store = IssueStore(snapshot_path(tmp_path, "my-work", demo.config().repo_names))
    store.replace(await demo.github().search_issues("assignee:@me"), requested_at=0.0)
    store.flush()

    app = LazyIssuesApp(demo.config(), Unreachable(viewer="me"), tmp_path)
    async with app.run_test(notifications=True) as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert listed(app) == MY_DEMO_ISSUES
        assert not refreshing(app)
        assert any("timed out" in text for text in notifications(app))


class Flaky(FakeGitHub):
    """A fake GitHub whose searches fail while `down`."""

    down = True

    async def search_issues(self, query: str) -> list[Issue]:
        if self.down:
            raise GitHubError("Couldn't reach GitHub: timed out")
        return await super().search_issues(query)


async def snapshot(tmp_path, loaded_at: datetime) -> None:
    """Save My Work's snapshot of the demo issues as loaded at `loaded_at`."""
    store = IssueStore(snapshot_path(tmp_path, "my-work", demo.config().repo_names))
    issues = await demo.github().search_issues("assignee:@me")
    store.replace(issues, requested_at=0.0, loaded_at=loaded_at)
    store.flush()


def status_line(app: LazyIssuesApp) -> str:
    return str(app.query_one("#my-work #filters", Static).render())


async def test_the_snapshots_age_shows_at_startup_and_after_each_refresh(tmp_path):
    await snapshot(tmp_path, datetime.now(UTC) - timedelta(minutes=4, seconds=30))

    github = Gated(demo.github())
    app = LazyIssuesApp(demo.config(), github, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert status_line(app) == "updated 4m ago"  # before GitHub answers

        github.gate.set()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert status_line(app) == "updated 0s ago"


async def test_the_age_keeps_counting_between_refreshes(tmp_path, monkeypatch):
    monkeypatch.setattr(issue_list, "AGE_TICK", 0.05)
    await snapshot(tmp_path, datetime.now(UTC) - timedelta(minutes=4, seconds=30))

    app = LazyIssuesApp(demo.config(), Gated(demo.github()), tmp_path)  # never answers
    async with app.run_test() as pilot:
        await pilot.pause()
        assert status_line(app) == "updated 4m ago"

        store = app.query_one("#my-work", IssueList).store
        store.loaded_at = datetime.now(UTC) - timedelta(minutes=5, seconds=30)
        await pilot.pause(0.2)
        assert status_line(app) == "updated 5m ago"


async def test_a_failed_refresh_says_when_the_cache_is_from_until_a_refresh_succeeds(tmp_path):
    await snapshot(tmp_path, datetime.now().astimezone().replace(hour=9, minute=12))

    github = Flaky(**vars(demo.github()))
    app = LazyIssuesApp(demo.config(), github, tmp_path)
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert status_line(app) == "refresh failed · cached 09:12"

        await refreshed(pilot)
        assert status_line(app) == "refresh failed · cached 09:12"  # still down

        github.down = False
        await refreshed(pilot)
        assert status_line(app) == "updated 0s ago"


async def test_r_refreshes_and_keeps_the_selected_issue_when_rows_move():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await select(pilot, "octo-dev/tidepool#12")

        github.issues.reverse()
        github.issues.insert(0, Issue("octo-dev/tidepool", 20, "New", "u", (demo.VIEWER,)))
        await refreshed(pilot)
        assert listed(app) == [
            "octo-dev/tidepool#20",
            "octo-dev/lanternfish#9",
            "octo-dev/lanternfish#4",
            "octo-dev/tidepool#12",
            "octo-dev/tidepool#15",
            "octo-dev/lanternfish#11",
        ]
        assert selected(app) == "octo-dev/tidepool#12"


async def test_when_the_selected_issue_leaves_the_cursor_stays_in_place():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await select(pilot, "octo-dev/tidepool#15")

        github.closed.add("octo-dev/tidepool#15")
        await refreshed(pilot)
        # Its row now holds the next group's header; the cursor doesn't jump to the top.
        assert table(app).cursor_row == 6


async def test_quitting_saves_the_snapshot_of_changes_still_waiting(tmp_path):
    app = LazyIssuesApp(demo.config(), demo.github(), tmp_path)
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()

    reopened = IssueStore(snapshot_path(tmp_path, "my-work", demo.config().repo_names))
    assert [issue.ref for issue in reopened.issues]


async def test_quitting_flushes_a_save_still_waiting_on_its_delay(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "SAVE_DELAY", 3600)  # the timer can't be what saved it
    path = snapshot_path(tmp_path, "my-work", demo.config().repo_names)
    app = LazyIssuesApp(demo.config(), demo.github(), tmp_path)
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        assert not path.exists()  # waiting on the delay

    assert [issue.ref for issue in IssueStore(path).issues]
