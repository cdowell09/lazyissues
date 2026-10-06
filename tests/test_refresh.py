"""My Work opens from the snapshot and refreshes in the background."""

import asyncio

from textual.app import App
from textual.pilot import Pilot
from textual.widgets import DataTable

from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import Issue, IssueDetail
from lazyissues.store import IssueStore, snapshot_path

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
