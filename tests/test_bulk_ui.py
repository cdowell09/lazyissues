"""Selecting issues in a list and acting on them all at once, against the fake GitHub."""

import asyncio
from collections.abc import Callable

from listed import plain, show_tab
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import DataTable, OptionList, Static
from textual.widgets.data_table import RowKey

from lazyissues import demo, mover
from lazyissues.app import LazyIssuesApp
from lazyissues.bulk_actions import BulkConfirm, BulkMenu, BulkSummary
from lazyissues.keys import KeysScreen
from lazyissues.mover import RejectedMoveBanner
from lazyissues.views.issue_list import IssueList, IssueTable

TIDE_12 = "octo-dev/tidepool#12"  # In Progress, from the `in-progress` label
TIDE_15 = "octo-dev/tidepool#15"  # In Review (two status labels)
LANTERN_4 = "octo-dev/lanternfish#4"  # In Progress on the project
LANTERN_9 = "octo-dev/lanternfish#9"  # not on the project: No status
LANTERN_11 = "octo-dev/lanternfish#11"  # Blocked on the project
TIDE_18 = "octo-dev/tidepool#18"  # unassigned


def table(app: App, view: str = "my-work") -> DataTable:
    return app.query_one(f"#{view} DataTable", DataTable)


def mark(app: App, row: RowKey, view: str = "my-work") -> str:
    """A row's checkbox, its first cell."""
    return str(table(app, view).get_row(row)[0])


def checked(app: App, view: str = "my-work") -> set[str]:
    """The keys of the issues whose rows are checked."""
    rows = table(app, view).ordered_rows
    return {str(row.key.value) for row in rows if row.key.value and mark(app, row.key, view) == "☑"}


def header_marks(app: App, view: str = "my-work") -> list[str]:
    return [
        mark(app, row.key, view) for row in table(app, view).ordered_rows if row.key.value is None
    ]


async def ready(pilot: Pilot) -> None:
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def until(pilot: Pilot, condition: Callable[[], bool]) -> None:
    """Let the app run until `condition` holds. (Waiting for every worker would wait on an
    open modal forever.)"""
    for _ in range(200):
        if condition():
            return
        await pilot.pause(0.01)
    raise AssertionError("timed out")


async def cursor_to(pilot: Pilot, key: str) -> None:
    await ready(pilot)
    table(pilot.app).move_cursor(row=table(pilot.app).get_row_index(key))
    await pilot.pause()


async def test_checked_issues_and_group_headers_are_tinted_to_the_end_of_the_row():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        for key in (TIDE_12, TIDE_15):
            await cursor_to(pilot, key)
            await pilot.press("space")
        await cursor_to(pilot, LANTERN_11)  # the cursor has its own color

        def background(row: int) -> object:
            """The background at the right edge of `row`, past its last column."""
            at = table(app).region
            return app.screen.get_style_at(at.right - 1, at.y + 1 + row).bgcolor  # 1: headers

        rows = table(app).ordered_rows
        headers = {background(i) for i, row in enumerate(rows) if row.key.value is None}
        checked = {background(table(app).get_row_index(key)) for key in (TIDE_12, TIDE_15)}
        plain = background(table(app).get_row_index(LANTERN_4))
        assert len(headers) == len(checked) == 1  # alike within each kind
        assert len(headers | checked | {plain}) == 3  # and unlike each other


async def test_space_checks_an_issue_or_a_whole_group_and_u_clears_them():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await cursor_to(pilot, TIDE_12)
        await pilot.press("space")
        assert checked(app) == {TIDE_12}
        await pilot.press("space")
        assert checked(app) == set()

        # On a group header, Space checks every issue in the group.
        table(app).move_cursor(row=table(app).get_row_index(TIDE_12) - 1)
        await pilot.press("space")
        assert checked(app) == {TIDE_12, LANTERN_4}
        assert header_marks(app) == ["☐", "☑", "☐", "☐"]

        await pilot.press("A")
        assert checked(app) == {TIDE_12, TIDE_15, LANTERN_4, LANTERN_9, LANTERN_11}
        # A search hides some, but they stay selected, and the count says so.
        await pilot.press("slash", *"glow", "enter")
        assert checked(app) == {LANTERN_4}
        filters = app.query_one("#my-work #filters", Static)
        assert str(filters.render()) == "search: glow  ·  selected: 5"
        await pilot.press("u")
        assert checked(app) == set()
        assert str(filters.render()) == "search: glow"


async def test_the_selection_survives_a_refresh():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await cursor_to(pilot, TIDE_15)
        await pilot.press("space")
        await pilot.press("r")
        await ready(pilot)
        assert checked(app) == {TIDE_15}


async def test_clicking_a_checkbox_toggles_its_issue_or_group_without_opening_or_folding():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await ready(pilot)
        at = table(app).get_row_index(TIDE_12)
        await pilot.click(table(app), offset=(1, at + 1))  # the checkbox, under the headers
        await pilot.click(table(app), offset=(1, at + 1))  # a second click doesn't open it
        await pilot.click(table(app), offset=(1, at + 1))
        assert checked(app) == {TIDE_12}
        assert app.screen is app.screen_stack[0]

        await pilot.click(table(app), offset=(1, at))  # its group's header
        assert checked(app) == {TIDE_12, LANTERN_4}
        assert header_marks(app)[1] == "☑"  # and the group stays unfolded


async def test_a_click_just_after_a_redraw_lands_on_the_checkbox_it_was_aimed_at():
    """A redraw (a click on a checkbox, a refresh) rebuilds the table, which finishes
    laying it out only once idle. A click before then must find the same cell, or a quick
    second click on a checkbox opens its issue instead."""
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await ready(pilot)
        region = table(app).region  # its checkbox, under the headers:
        x, y = region.x + 1, region.y + table(app).get_row_index(TIDE_12) + 1
        aimed_at = app.screen.get_style_at(x, y).meta
        app.query_one("#my-work", IssueList).show()
        assert app.screen.get_style_at(x, y).meta == aimed_at


def groups(app: App, view: str = "my-work") -> dict[str, list[str]]:
    """Each group's issue keys, by the group's name."""
    shown: dict[str, list[str]] = {}
    group: list[str] = []
    for row, text in zip(table(app, view).ordered_rows, plain(table(app, view)), strict=True):
        if row.key.value is None:
            group = shown.setdefault(text.rsplit(" (", 1)[0], [])
        else:
            group.append(row.key.value)
    return shown


def options(app: App) -> list[str]:
    menu = app.screen.query_one(OptionList)
    return [str(menu.get_option_at_index(i).prompt) for i in range(menu.option_count)]


async def choose(pilot: Pilot, prompt: str) -> None:
    """Take the option of the open bulk menu that reads `prompt`, or starts with it."""
    await until(pilot, lambda: isinstance(pilot.app.screen, BulkMenu))
    await until(pilot, lambda: any(o.startswith(prompt) for o in options(pilot.app)))
    shown = options(pilot.app)
    choice = prompt if prompt in shown else next(o for o in shown if o.startswith(prompt))
    pilot.app.screen.query_one(OptionList).highlighted = shown.index(choice)
    await pilot.press("enter")


def report(app: App) -> list[str]:
    return str(app.screen.query_one("#report", Static).render()).splitlines()


async def test_bulk_move_moves_what_it_can_across_label_and_project_repos_and_lists_skips():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await ready(pilot)
        await pilot.press("A", "B")
        await choose(pilot, "Move")
        await choose(pilot, "Move to In Progress  (3 of 5 can)")

        await until(pilot, lambda: isinstance(app.screen, BulkConfirm))
        assert report(app) == [
            "Will change (3)",
            "  lanternfish#9  Count lanterns per reef",
            "  lanternfish#11  Wait for the depth sensor API",
            "  tidepool#15  Add a weekly digest of high tides",
            "Skipped (2)",
            "  lanternfish#4  Already in In Progress",
            "  tidepool#12  Already in In Progress",
        ]
        await pilot.press("enter")

        await until(pilot, lambda: isinstance(app.screen, BulkSummary))
        assert report(app)[0] == "Done (3)"
        assert report(app)[4] == "Skipped (2)"
        await pilot.press("escape")
        await ready(pilot)
        assert sorted(groups(app)["In Progress"]) == sorted(
            [TIDE_12, TIDE_15, LANTERN_4, LANTERN_9, LANTERN_11]
        )
        assert checked(app) == set()  # a bulk action clears the selection
        [counting] = [i for i in await github.search_issues("is:issue") if i.key == LANTERN_9]
        assert counting.project_statuses == {demo.PROJECT: "In Progress"}


async def test_a_bulk_move_github_refuses_lists_its_error_and_keeps_the_status():
    github = demo.github()
    github.read_only.add("octo-dev/lanternfish")
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await ready(pilot)
        await pilot.press("A", "B")
        await choose(pilot, "Move")
        await choose(pilot, "Move to In Progress  (3 of 5 can)")
        await until(pilot, lambda: isinstance(app.screen, BulkConfirm))
        await pilot.press("enter")

        await until(pilot, lambda: isinstance(app.screen, BulkSummary))
        refused = "Resource not accessible by integration: octo-dev/lanternfish"
        assert report(app)[:5] == [
            "Done (1)",
            "  tidepool#15  Add a weekly digest of high tides",
            "Failed (2)",
            f"  lanternfish#9  {refused}",
            f"  lanternfish#11  {refused}",
        ]
        await pilot.press("escape")
        await ready(pilot)
        assert not isinstance(app.screen, RejectedMoveBanner)  # the summary listed them
        assert app.moves.rejected == []
        assert LANTERN_9 in groups(app)["No status"]
        assert TIDE_15 in groups(app)["In Progress"]


async def open_tab(pilot: Pilot, view: str) -> None:
    """Switch to the tab holding `view` and focus its list, as a user would."""
    await show_tab(pilot, view)
    table(pilot.app, view).focus()
    await ready(pilot)


async def test_bulk_assign_offers_the_team_first_and_reports_each_issue():
    github = demo.github()
    github.read_only.add("octo-dev/lanternfish")
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await ready(pilot)
        await open_tab(pilot, "unassigned")
        await pilot.press("A", "B")
        await choose(pilot, "Assign")
        await until(pilot, lambda: len(options(app)) == 4)
        assert options(app) == [
            "Assign to sam-reef  (2 of 2 can)",
            "Assign to mo-kelp  (1 of 2 can)",
            "Assign to octo-dev  (2 of 2 can)",
            "Assign to ray-coral  (1 of 2 can)",
        ]
        await choose(pilot, "Assign to sam-reef  (2 of 2 can)")
        await until(pilot, lambda: isinstance(app.screen, BulkConfirm))
        await pilot.press("enter")

        await until(pilot, lambda: isinstance(app.screen, BulkSummary))
        refused = "Resource not accessible by integration: octo-dev/lanternfish"
        assert report(app) == [
            "Done (1)",
            "  tidepool#18  Document the import format",
            "Failed (1)",
            f"  lanternfish#13  {refused}",
        ]
        await pilot.press("escape")
        await ready(pilot)
        # Shown where it's listed, as a single assignment is.
        assert str(table(app, "unassigned").get_row(TIDE_18)[4]) == "sam-reef"
        [docs] = [i for i in await github.search_issues("is:issue") if i.key == TIDE_18]
        assert docs.assignees == ("sam-reef",)


async def test_a_bulk_assign_is_refused_for_a_login_github_wont_assign_in_that_repo():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await ready(pilot)
        await open_tab(pilot, "unassigned")
        await pilot.press("A", "B")
        await choose(pilot, "Assign")
        await choose(pilot, "Assign to sam-reef")
        await until(pilot, lambda: isinstance(app.screen, BulkConfirm))
        github.assignable["octo-dev/tidepool"].remove("sam-reef")  # changed since it was listed
        await pilot.press("enter")

        await until(pilot, lambda: isinstance(app.screen, BulkSummary))
        lines = report(app)
        assert "Failed (1)" in lines
        assert "  tidepool#18  sam-reef can't be assigned to issues in octo-dev/tidepool." in lines
        [docs] = [i for i in await github.search_issues("is:issue") if i.key == TIDE_18]
        assert docs.assignees == ()


async def test_question_mark_lists_the_selection_and_bulk_keys():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(100, 140)) as pilot:
        await ready(pilot)
        await pilot.press("question_mark")
        await until(pilot, lambda: isinstance(app.screen, KeysScreen))
        text = "\n".join(str(widget.render()) for widget in app.screen.query(Static))
        lines = {" ".join(line.split()) for line in text.splitlines()}
        assert {
            "space Select",
            "A Select all shown",
            "u Clear selection",
            "B Bulk move or assign",
            "Bulk action menus",
            "Confirming a bulk action",
            "⏎ Send",
            "Bulk action summary",
            "click ☐ Select an issue, or on a group header its group",
        } <= lines


async def test_cancelling_the_confirmation_changes_nothing():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await cursor_to(pilot, TIDE_12)
        await pilot.press("space", "B")
        await choose(pilot, "Move")
        await choose(pilot, "Move to Todo  (1 of 1 can)")
        await until(pilot, lambda: isinstance(app.screen, BulkConfirm))
        await pilot.press("escape")
        await ready(pilot)
        assert TIDE_12 in groups(app)["In Progress"]
        assert checked(app) == {TIDE_12}


async def test_selecting_updates_checkboxes_in_place_and_keeps_the_cursor(monkeypatch):
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await cursor_to(pilot, TIDE_15)
        rebuilds: list[None] = []
        clear = IssueTable.clear
        monkeypatch.setattr(IssueTable, "clear", lambda *a, **k: rebuilds.append(clear(*a, **k)))
        for key in ("space", "A", "u", "space"):
            await pilot.press(key)
        assert checked(app) == {TIDE_15}
        assert table(app).cursor_row == table(app).get_row_index(TIDE_15)
        assert rebuilds == []
        filters = app.query_one("#my-work #filters", Static)
        assert str(filters.render()) == "selected: 1"


async def redraws_during_bulk(pilot: Pilot, monkeypatch, *prompts: str) -> int:
    """Press A, B, take each of `prompts`, confirm, and count the list rebuilds until the
    summary shows in the my-work list."""
    rebuilds: list[None] = []
    clear = IssueTable.clear

    def counting(table: IssueTable, *args, **kwargs):
        if table.parent and table.parent.id == "my-work":  # the tab in view
            rebuilds.append(None)
        return clear(table, *args, **kwargs)

    monkeypatch.setattr(IssueTable, "clear", counting)
    await ready(pilot)
    await pilot.press("A", "B")
    for prompt in prompts:
        await choose(pilot, prompt)
    await until(pilot, lambda: isinstance(pilot.app.screen, BulkConfirm))
    rebuilds.clear()
    await pilot.press("enter")
    await until(pilot, lambda: isinstance(pilot.app.screen, BulkSummary))
    return len(rebuilds)


async def test_a_bulk_move_redraws_each_list_when_it_starts_and_when_it_ends(monkeypatch):
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        moved = await redraws_during_bulk(
            pilot, monkeypatch, "Move", "Move to In Progress  (3 of 5 can)"
        )
        assert moved == 2
        assert report(app)[0] == "Done (3)"


async def test_a_bulk_assign_redraws_each_list_once(monkeypatch):
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await ready(pilot)
        assigned = await redraws_during_bulk(pilot, monkeypatch, "Assign", "Assign to sam-reef")
        assert assigned == 1
        assert report(app)[0].startswith("Done (")


async def test_a_bulk_move_shows_every_issue_pending_while_it_runs(monkeypatch):
    release = asyncio.Event()
    send = mover.send

    async def slow(*args, **kwargs):
        await release.wait()
        return await send(*args, **kwargs)

    monkeypatch.setattr(mover, "send", slow)
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await ready(pilot)
        await pilot.press("A", "B")
        await choose(pilot, "Move")
        await choose(pilot, "Move to In Progress")
        await until(pilot, lambda: isinstance(app.screen, BulkConfirm))
        await pilot.press("enter")

        moving = (TIDE_15, LANTERN_9, LANTERN_11)
        await until(pilot, lambda: all(app.moves.pending(key) for key in moving))
        await until(pilot, lambda: all("⋯" in str(table(app).get_row(k)) for k in moving))
        release.set()
        await until(pilot, lambda: isinstance(app.screen, BulkSummary))
        assert not any(app.moves.pending(key) for key in moving)
