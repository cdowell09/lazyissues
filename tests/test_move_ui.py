"""Moving issues from My Work and the detail, against the fake GitHub."""

import asyncio
import webbrowser
from collections.abc import Callable, Sequence

from textual.app import App
from textual.pilot import Pilot
from textual.widgets import DataTable, Input, OptionList, Static

from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.detail import IssueDetailScreen
from lazyissues.fake import FakeGitHub
from lazyissues.models import CloseReason, Issue
from lazyissues.move_picker import MovePicker
from lazyissues.mover import RejectedMoveBanner
from lazyissues.statuses import StatusRules
from lazyissues.views.issue_list import IssueList

TIDE_12 = "octo-dev/tidepool#12"  # In Progress, from the `in-progress` label
LANTERN_9 = "octo-dev/lanternfish#9"  # project-backed, not on the project


class Gated(FakeGitHub):
    """The demo GitHub. Writes wait for `writes`; a search answers as of when it was
    sent, but only once `searches` is set."""

    def __init__(self) -> None:
        fake = demo.github()
        super().__init__(**vars(fake))
        self.writes = asyncio.Event()
        self.writes.set()
        self.searches = asyncio.Event()
        self.searches.set()

    async def search_issues(self, query: str) -> list[Issue]:
        answer = await super().search_issues(query)
        await self.searches.wait()
        return answer

    async def add_label(self, repo: str, number: int, name: str) -> None:
        await self.writes.wait()
        await super().add_label(repo, number, name)

    async def remove_labels(self, repo: str, number: int, names: Sequence[str]) -> None:
        await self.writes.wait()
        await super().remove_labels(repo, number, names)


def table(app: App) -> DataTable:
    return app.query_one("#my-work DataTable", DataTable)


def groups(app: LazyIssuesApp) -> dict[str, list[str]]:
    """Each status group's issue keys, by the group's name."""
    shown: dict[str, list[str]] = {}
    group: list[str] = []
    for row in table(app).ordered_rows:
        if row.key.value is None:
            header = str(table(app).get_row(row.key)[1])
            group = shown.setdefault(header.rsplit(" (", 1)[0], [])
        else:
            group.append(row.key.value)
    return shown


def cell(app: LazyIssuesApp, key: str) -> str:
    return str(table(app).get_row(key)[1])


async def ready(pilot: Pilot) -> None:
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def select(pilot: Pilot, key: str) -> None:
    await ready(pilot)
    table(pilot.app).move_cursor(row=table(pilot.app).get_row_index(key))
    await pilot.pause()


async def until(pilot: Pilot, condition: Callable[[], bool]) -> None:
    """Let the app run until `condition` holds. (Waiting for every worker would wait on an
    open picker forever.)"""
    for _ in range(200):
        if condition():
            return
        await pilot.pause(0.01)
    raise AssertionError("timed out")


async def picker(pilot: Pilot) -> None:
    await until(pilot, lambda: isinstance(pilot.app.screen, MovePicker))


async def choose(pilot: Pilot, label: str) -> None:
    """Highlight the picker's option starting with `label` and take it."""
    await picker(pilot)
    picker_screen = pilot.app.screen
    assert isinstance(picker_screen, MovePicker)
    options = picker_screen.query_one(OptionList)
    prompts = [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]
    options.highlighted = next(i for i, p in enumerate(prompts) if p.startswith(label))
    await pilot.press("enter")


def picker_options(app: LazyIssuesApp) -> list[str]:
    options = app.screen.query_one(OptionList)
    return [str(options.get_option_at_index(i).prompt) for i in range(options.option_count)]


async def test_m_offers_the_moves_the_issue_can_make():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await select(pilot, TIDE_12)
        await pilot.press("m")
        await picker(pilot)
        assert picker_options(app) == [
            "Todo",
            "In Progress  (p) · current",
            "In Review",
            "Close as completed",
            "Close as not planned",
            "Close as duplicate of…",
        ]
        await pilot.press("escape")
        assert not isinstance(app.screen, MovePicker)


async def test_a_move_shows_as_pending_until_github_confirms_it():
    github = Gated()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await select(pilot, TIDE_12)
        github.writes.clear()
        await pilot.press("m")
        await choose(pilot, "In Review")
        await pilot.pause()

        assert "⋯ In Review" in cell(app, TIDE_12)
        assert TIDE_12 in groups(app)["In Progress"]

        github.writes.set()
        await ready(pilot)
        assert "⋯" not in cell(app, TIDE_12)
        assert TIDE_12 in groups(app)["In Review"]
        # Every tab listing the issue shows the move, not only the one it was made in.
        rules = StatusRules(demo.config())
        stores = [view.store for view in app.query(IssueList)]
        holding = [s for s in stores if any(i.key == TIDE_12 for i in s.issues)]
        assert len(holding) == 3  # My Work, Team and Milestones
        for store in holding:
            [moved] = [i for i in store.issues if i.key == TIDE_12]
            assert rules.status_of(moved).name == "In Review"


async def test_a_move_on_a_project_adds_the_issue_and_sets_its_status():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await select(pilot, LANTERN_9)
        await pilot.press("m")
        await picker(pilot)
        assert "Blocked" in picker_options(app)
        assert "Done" not in picker_options(app)  # reached by closing
        await choose(pilot, "Blocked")
        await ready(pilot)

        assert LANTERN_9 in groups(app)["Blocked"]
        [moved] = [i for i in await github.search_issues("is:issue") if i.key == LANTERN_9]
        assert moved.project_statuses == {demo.PROJECT: "Blocked"}


async def test_the_uppercase_shortcut_moves_at_once():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await select(pilot, "octo-dev/tidepool#15")
        await pilot.press("P")
        await ready(pilot)
        assert not isinstance(app.screen, MovePicker)
        assert "octo-dev/tidepool#15" in groups(app)["In Progress"]


async def test_the_lowercase_shortcut_opens_the_picker_on_that_move():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await select(pilot, "octo-dev/tidepool#15")
        await pilot.press("p")
        await picker(pilot)
        options = app.screen.query_one(OptionList)
        assert options.highlighted == 1
        await pilot.press("enter")
        await ready(pilot)
        assert "octo-dev/tidepool#15" in groups(app)["In Progress"]


async def test_a_rejected_move_keeps_the_status_and_shows_the_error_until_dismissed(
    monkeypatch,
):
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    github = demo.github()
    github.read_only.add("octo-dev/tidepool")
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await select(pilot, TIDE_12)
        await pilot.press("m")
        await choose(pilot, "In Review")
        await ready(pilot)

        assert isinstance(app.screen, RejectedMoveBanner)
        banner = str(app.screen.query_one("#error", Static).render())
        assert "Couldn't move tidepool#12 to In Review" in banner
        assert "Resource not accessible" in banner
        await pilot.press("o")
        assert opened == ["https://github.com/octo-dev/tidepool/issues/12"]

        await pilot.press("escape")
        assert not isinstance(app.screen, RejectedMoveBanner)
        assert TIDE_12 in groups(app)["In Progress"]
        assert app.moves.rejected == []


async def test_a_refresh_requested_before_a_confirmed_move_does_not_revert_it():
    github = Gated()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await select(pilot, TIDE_12)
        github.searches.clear()
        await pilot.press("r")  # answers with the issue as it is now, but late
        await pilot.pause()
        await pilot.press("m")
        await choose(pilot, "In Review")
        await until(pilot, lambda: TIDE_12 in groups(app).get("In Review", []))

        github.searches.set()
        await ready(pilot)
        assert TIDE_12 in groups(app)["In Review"]


async def test_close_as_duplicate_asks_for_the_original():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await select(pilot, TIDE_12)
        await pilot.press("m")
        await choose(pilot, "Close as duplicate")
        await pilot.pause()

        prompt = app.screen.query_one(Input)
        prompt.value = "soon"
        await pilot.press("enter")
        assert isinstance(app.screen, MovePicker)  # not an issue reference

        prompt.value = "#15"
        await pilot.press("enter")
        await ready(pilot)
        assert TIDE_12 not in table(app).rows  # closed issues leave My Work
        assert github.close_reasons[TIDE_12] == (
            CloseReason.DUPLICATE,
            "octo-dev/tidepool#15",
        )


async def test_m_in_the_detail_moves_its_issue_and_reopen_brings_it_back():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await select(pilot, TIDE_12)
        await pilot.press("enter")
        await ready(pilot)
        assert isinstance(app.screen, IssueDetailScreen)

        await pilot.press("m")
        await choose(pilot, "Close as completed")
        await ready(pilot)
        assert isinstance(app.screen, IssueDetailScreen)
        assert "State: Closed" in str(app.screen.query_one(".fields", Static).render())

        await pilot.press("m")
        await picker(pilot)
        assert picker_options(app)[-1] == "Reopen"
        await choose(pilot, "Reopen")
        await ready(pilot)
        assert "State: Open" in str(app.screen.query_one(".fields", Static).render())


async def test_an_issue_has_one_move_in_flight_at_a_time():
    github = Gated()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test(notifications=True) as pilot:
        await select(pilot, "octo-dev/tidepool#15")
        github.writes.clear()
        await pilot.press("P")
        await until(pilot, lambda: "⋯" in cell(app, "octo-dev/tidepool#15"))

        await pilot.press("m")
        await pilot.pause()
        assert not isinstance(app.screen, MovePicker)
        toasts = [str(toast.render()) for toast in app.screen.query("Toast")]
        assert any("still moving" in toast for toast in toasts)

        github.writes.set()
        await ready(pilot)
        assert "octo-dev/tidepool#15" in groups(app)["In Progress"]
