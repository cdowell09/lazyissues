"""The list tabs: Team, Unassigned, and the keys every list tab shares."""

from listed import drawn, plain, show_tab
from textual.pilot import Pilot
from textual.widgets import DataTable, TabbedContent

from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.config import Config, Repo, Status
from lazyissues.detail import IssueDetailScreen
from lazyissues.fake import FakeGitHub
from lazyissues.models import Issue
from lazyissues.views import issue_list
from lazyissues.views.issue_list import IssueList


def table(app: LazyIssuesApp, view: str) -> DataTable:
    return app.query_one(f"#{view} DataTable", DataTable)


def firsts(app: LazyIssuesApp, view: str) -> list[str]:
    """The first cell of every row: group headers and issue refs."""
    return plain(table(app, view))


async def open_tab(pilot: Pilot, view: str) -> None:
    """Switch to the tab holding `view` and focus its list, as a user would."""
    app = pilot.app
    tabs = app.query_one(TabbedContent)
    pane = app.query_one(f"#{view}").parent
    assert pane is not None and pane.id is not None
    tabs.active = pane.id
    await pilot.pause()
    app.query_one(f"#{view} DataTable").focus()
    await pilot.pause()


async def settled(pilot: Pilot) -> None:
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def test_team_groups_open_issues_by_member_ordered_by_active_issues():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await show_tab(pilot, "team")
        # Issues indent under their group; a fold arrow has a column of its own.
        assert drawn(table(app, "team")) == [
            "▾ octo-dev (5)",  # the viewer, though not on the roster; two active issues
            "    lanternfish#4",
            "    tidepool#12",
            "  ▾ lanternfish#9",  # a parent, unfolded
            "    └ lanternfish#11",  # a sub-issue under its parent
            "    tidepool#15 ⚠",
            "▾ sam-reef (2)",  # one active issue
            "    lanternfish#4",  # shared with the viewer
            "    lanternfish#9 → lanternfish#7",  # its parent is in another group
            "▾ mo-kelp (0)",  # on the roster with nothing assigned
        ]


async def test_unassigned_lists_open_issues_without_an_assignee_by_status():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await show_tab(pilot, "unassigned")
        assert firsts(app, "unassigned") == [
            "No status (1)",
            "tidepool#18",
            "Todo (1)",
            "lanternfish#13",
        ]


async def test_d_shows_issues_closed_within_the_done_window():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await open_tab(pilot, "my-work")
        await pilot.press("d")
        await settled(pilot)
        # tidepool#10 closed two days ago; lanternfish#2 forty days ago, outside the window.
        assert firsts(app, "my-work")[-2:] == ["Done (1)", "tidepool#10"]

        await pilot.press("d")
        assert "Done (1)" not in firsts(app, "my-work")


async def test_search_focus_folds_and_repo_filter_are_remembered_per_tab():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await open_tab(pilot, "my-work")
        await pilot.press("slash", *"glow", "enter")
        assert firsts(app, "my-work") == ["In Progress (1)", "lanternfish#4"]

        await open_tab(pilot, "team")
        await pilot.press("slash", *"mo-k", "enter")
        assert firsts(app, "team") == ["mo-kelp (0)"]

        await open_tab(pilot, "my-work")
        assert firsts(app, "my-work") == ["In Progress (1)", "lanternfish#4"]
        await pilot.press("escape")  # clears the search
        assert len(firsts(app, "my-work")) == 9

        await pilot.press("f", "f")  # No status, then In Progress
        assert firsts(app, "my-work") == ["In Progress (2)", "tidepool#12", "lanternfish#4"]
        await pilot.press("F", "F")  # back through No status to every status
        assert len(firsts(app, "my-work")) == 9

        await pilot.press("R")  # the first repo in the repo set
        assert firsts(app, "my-work") == [
            "In Progress (1)",
            "tidepool#12",
            "In Review (1)",
            "tidepool#15 ⚠",
        ]
        await pilot.press("z")  # folds the group under the cursor
        assert drawn(table(app, "my-work"))[0] == "▸ In Progress (1)"
        await pilot.press("Z")
        assert drawn(table(app, "my-work")) == ["▸ In Progress (1)", "▸ In Review (1)"]
        await pilot.press("Z")
        assert len(firsts(app, "my-work")) == 4

        assert firsts(app, "team") == ["mo-kelp (0)"]


async def test_r_refreshes_the_tab_on_screen():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await settled(pilot)
        await open_tab(pilot, "unassigned")
        github.closed.add("octo-dev/tidepool#18")
        await pilot.press("r")
        await show_tab(pilot, "unassigned")
        assert firsts(app, "unassigned") == ["Todo (1)", "lanternfish#13"]


async def test_enter_opens_the_detail_from_any_list_tab():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await open_tab(pilot, "team")
        await pilot.press("down", "enter")
        await pilot.pause()
        assert isinstance(app.screen, IssueDetailScreen)
        assert app.screen.issues[app.screen.index].key == "octo-dev/lanternfish#4"


async def test_z_on_a_sub_issue_folds_its_parent_and_the_total_still_counts_it():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await open_tab(pilot, "team")
        await pilot.press("down", "down", "down", "down", "z")  # on lanternfish#11
        assert drawn(table(app, "team"))[:5] == [
            "▾ octo-dev (5)",
            "    lanternfish#4",
            "    tidepool#12",
            "  ▸ lanternfish#9",
            "    tidepool#15 ⚠",
        ]
        team = table(app, "team")
        assert drawn(team)[team.cursor_row] == "  ▸ lanternfish#9"

        await pilot.press("z")  # on the parent unfolds it
        assert drawn(table(app, "team"))[4] == "    └ lanternfish#11"


async def test_typing_a_search_filters_once_after_a_pause(monkeypatch):
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        view = app.query_one("#my-work", IssueList)
        draws: list[None] = []
        show = view.show
        monkeypatch.setattr(view, "show", lambda: draws.append(show()))
        # A pause no keystroke can outlast, however slow the machine: typing never draws.
        monkeypatch.setattr(issue_list, "SEARCH_PAUSE", 60)
        await pilot.press("slash", *"glo")
        assert draws == []
        # The pause after the last keystroke filters once.
        monkeypatch.setattr(issue_list, "SEARCH_PAUSE", 0.05)
        await pilot.press("w")
        await pilot.pause(0.3)
        assert len(draws) == 1
        assert firsts(app, "my-work") == ["In Progress (1)", "lanternfish#4"]


LONG = "A title long enough to push every column after it off an eighty-column terminal"


def long_titled_app() -> LazyIssuesApp:
    url = "https://github.com/o/r/issues/1"
    issue = Issue("o/r", 1, LONG, url, ("me",), ("todo", "bug"))
    config = Config(repos=[Repo("o/r")], statuses=[Status("Todo")])
    return LazyIssuesApp(config, FakeGitHub(viewer="me", issues=[issue]))


def shown(app: LazyIssuesApp) -> tuple[str, str]:
    """My Work's column headings and its issue's row, as the terminal shows them."""
    listed = table(app, "my-work")
    lines = [listed.render_line(y).text for y in range(listed.size.height)]
    return lines[0], next(line for line in lines if "r#1" in line)


async def test_at_80_columns_every_column_shows_and_a_long_title_ends_in_an_ellipsis():
    app = long_titled_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot)
        headings, row = shown(app)
        assert headings.split() == ["Issue", "Title", "Status", "Assignees", "Labels"]
        assert row.rstrip().split("  ")[-1].strip() == "todo, bug"  # the last column, whole
        title = row.split("r#1")[1].split("…")[0].strip()
        assert LONG.startswith(title) and len(title) > 10  # cut short, with room to read


async def test_resizing_refits_the_columns():
    app = long_titled_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot)
        await pilot.resize_terminal(160, 24)
        await pilot.pause()
        assert LONG in shown(app)[1]  # the whole title, uncut
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert LONG not in shown(app)[1] and "…" in shown(app)[1]
        assert shown(app)[1].rstrip().endswith("todo, bug")


# Natural widths of the checkbox, Issue, Title, Status, Assignees and Labels columns.
NATURAL = [1, 15, 40, 11, 9, 12]  # 48 without Title


def test_title_takes_the_room_the_other_columns_leave():
    assert issue_list.fit(NATURAL, 100) == [1, 15, 52, 11, 9, 12]


def test_title_keeps_its_minimum_when_the_others_just_fit_beside_it():
    space = 48 + issue_list.MIN_TITLE
    assert issue_list.fit(NATURAL, space) == [1, 15, issue_list.MIN_TITLE, 11, 9, 12]


def test_one_cell_short_cuts_the_widest_other_column():
    space = 48 + issue_list.MIN_TITLE - 1
    assert issue_list.fit(NATURAL, space) == [1, 14, issue_list.MIN_TITLE, 11, 9, 12]
