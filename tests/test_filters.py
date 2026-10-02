"""The Filters tab: saved filters in a sidebar, each run into its own grouped list."""

import asyncio
from dataclasses import replace

from textual.pilot import Pilot
from textual.widgets import DataTable, OptionList, TabbedContent

from lazyissues import config as config_module
from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.config import SavedFilter
from lazyissues.fake import FakeGitHub
from lazyissues.forms.edit import EditForm
from lazyissues.models import Issue
from lazyissues.views.filters import ConfirmDelete, FilterForm, FilterResults


class Counting(FakeGitHub):
    """A fake GitHub that records every search it answers."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.searches: list[str] = []

    async def search_issues(self, query: str) -> list[Issue]:
        self.searches.append(query)
        return await super().search_issues(query)


class Stalled(FakeGitHub):
    """A fake GitHub whose searches never answer."""

    async def search_issues(self, query: str) -> list[Issue]:
        await asyncio.Event().wait()
        return []


def results(app: LazyIssuesApp) -> FilterResults:
    """The results of the filter that ran last."""
    shown = [view for view in app.query(FilterResults) if view.display]
    assert len(shown) == 1
    return shown[0]


def firsts(app: LazyIssuesApp) -> list[str]:
    """The first cell of every row of the shown results: group headers and issue refs."""
    table = results(app).query_one(DataTable)
    return [str(table.get_row_at(i)[1]) for i in range(table.row_count)]


async def settled(pilot: Pilot) -> None:
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def show_filters(pilot: Pilot) -> None:
    """Switch to the Filters tab and focus its sidebar, as a user would."""
    pane = pilot.app.query_one("#filters").parent
    assert pane is not None and pane.id is not None
    pilot.app.query_one(TabbedContent).active = pane.id
    await pilot.pause()
    pilot.app.query_one("#filters OptionList").focus()
    await pilot.pause()


async def open_filters(pilot: Pilot) -> None:
    """Show the Filters tab once the first filter's results are in."""
    await show_filters(pilot)
    await settled(pilot)


async def test_the_first_saved_filter_runs_and_its_issues_are_grouped_by_status():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await open_filters(pilot)
        assert firsts(app) == [
            "No status (1)",
            "lanternfish#9",
            "Todo (1)",
            "lanternfish#9 → lanternfish#7",  # a sub-issue, its parent in another group
        ]
        sidebar = app.query_one("#filters OptionList", OptionList)
        assert [str(sidebar.get_option_at_index(i).prompt) for i in range(3)] == [
            "Ready for me",
            "Needs triage",
            "Tidepool bugs",
        ]


async def test_enter_runs_the_selected_filter_and_tab_moves_between_sidebar_and_results():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await open_filters(pilot)
        await pilot.press("down", "enter")
        await settled(pilot)
        assert firsts(app) == ["Todo (1)", "lanternfish#13"]

        await pilot.press("tab")
        assert app.focused is results(app).query_one(DataTable)
        await pilot.press("shift+tab")
        assert app.focused is app.query_one("#filters OptionList")
        await pilot.press("tab", "shift+tab", "tab")
        assert app.focused is results(app).query_one(DataTable)


async def test_a_query_with_its_own_repo_and_state_runs_as_written():
    config = replace(
        demo.config(),
        filters=[
            SavedFilter("Lanternfish", "repo:octo-dev/lanternfish no:assignee"),
            SavedFilter("Closed", "is:closed"),
        ],
    )
    github = demo.github()
    # Closed, but outside the repo set the unscoped `is:closed` filter is limited to.
    github.issues.append(Issue("octo-dev/elsewhere", 1, "Elsewhere", "u", closed=True))
    app = LazyIssuesApp(config, github)
    async with app.run_test() as pilot:
        await open_filters(pilot)
        assert firsts(app) == ["Todo (1)", "lanternfish#13"]

        await pilot.press("down", "enter")
        await settled(pilot)
        # Shown without `d`, and closed 2 and 40 days ago: the query's own `is:closed`
        # replaces the done window.
        assert firsts(app) == ["Done (2)", "tidepool#10", "lanternfish#2"]

        await pilot.press("tab", "d")  # `d` still hides them
        assert firsts(app) == [""]


async def test_an_invalid_query_shows_githubs_error_and_the_app_carries_on():
    config = replace(demo.config(), filters=[SavedFilter("Broken", 'label:"needs triage')])
    app = LazyIssuesApp(config, demo.github())
    async with app.run_test(notifications=True) as pilot:
        await open_filters(pilot)
        toasts = [str(toast.render()) for toast in app.screen.query("Toast")]
        assert any("Invalid search query" in text for text in toasts)
        # With nothing loaded, the error stays in the list once the toast goes.
        message = str(results(app).query_one(DataTable).get_row_at(0)[2])
        assert "Invalid search query" in message
        assert app.is_running


async def test_r_refreshes_only_the_filter_on_screen():
    github = Counting(**vars(demo.github()))
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await open_filters(pilot)
        await pilot.press("down", "enter")
        await settled(pilot)
        searched = list(github.searches)

        await pilot.press("r")
        await settled(pilot)
        new = github.searches[len(searched) :]
        assert new and all("label:needs-triage" in query for query in new)


def names(app: LazyIssuesApp) -> list[str]:
    sidebar = app.query_one("#filters OptionList", OptionList)
    return [str(option.prompt) for option in sidebar.options]


async def test_n_saves_a_new_filter_to_config_and_runs_it(tmp_path):
    path = tmp_path / "config.toml"
    config_module.save(demo.config(), path)
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=path)
    async with app.run_test() as pilot:
        await open_filters(pilot)
        await pilot.press("n", *"Mine", "tab", *"label:bug assignee:@me", "enter")
        await settled(pilot)
        assert names(app)[-1] == "Mine"
        assert firsts(app) == ["In Progress (1)", "tidepool#12"]
    saved = config_module.load(path).filters
    assert saved[-1] == SavedFilter("Mine", "label:bug assignee:@me")


async def test_a_filter_needs_a_name_and_a_query_and_esc_cancels(tmp_path):
    path = tmp_path / "config.toml"
    config_module.save(demo.config(), path)
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=path)
    async with app.run_test() as pilot:
        await open_filters(pilot)
        await pilot.press("n", *"Mine", "enter")
        assert isinstance(app.screen, FilterForm)
        await pilot.press("escape")
        assert not isinstance(app.screen, FilterForm)
        assert names(app) == ["Ready for me", "Needs triage", "Tidepool bugs"]
    assert config_module.load(path).filters == demo.config().filters


async def test_e_edits_and_x_deletes_the_highlighted_filter_after_confirming(tmp_path):
    path = tmp_path / "config.toml"
    config_module.save(demo.config(), path)
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=path)
    async with app.run_test() as pilot:
        await open_filters(pilot)
        await pilot.press("down", "e", "tab", "end", *" no:assignee", "enter")
        await settled(pilot)
        assert firsts(app) == ["Todo (1)", "lanternfish#13"]

        await pilot.press("up", "x", "n")  # keeps it
        assert len(names(app)) == 3
        await pilot.press("x", "y")
        await settled(pilot)
        assert names(app) == ["Needs triage", "Tidepool bugs"]
    assert config_module.load(path).filters == [
        SavedFilter("Needs triage", "label:needs-triage no:assignee"),
        SavedFilter("Tidepool bugs", "repo:octo-dev/tidepool label:bug"),
    ]


async def test_n_e_and_x_act_only_in_the_sidebar(tmp_path):
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await open_filters(pilot)
        await pilot.press("tab", "n", "x")
        assert not isinstance(app.screen, FilterForm | ConfirmDelete)


async def test_each_filter_opens_from_its_own_snapshot_on_the_next_launch(tmp_path):
    first = LazyIssuesApp(demo.config(), demo.github(), tmp_path)
    async with first.run_test() as pilot:
        await open_filters(pilot)
        await pilot.press("down", "enter")
        await settled(pilot)

    app = LazyIssuesApp(demo.config(), Stalled(**vars(demo.github())), tmp_path)
    async with app.run_test() as pilot:
        await show_filters(pilot)
        assert firsts(app) == [
            "No status (1)",
            "lanternfish#9",
            "Todo (1)",
            "lanternfish#9 → lanternfish#7",  # a sub-issue, its parent in another group
        ]
        await pilot.press("down", "enter")
        await pilot.pause()
        assert firsts(app) == ["Todo (1)", "lanternfish#13"]


async def test_the_writing_keys_act_on_the_issue_in_the_results_and_e_edits_it():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await open_filters(pilot)
        await pilot.press("tab", "down")  # lanternfish#9, under "No status"

        await pilot.press("C", "o", "k", "enter")
        await settled(pilot)
        [*_, comment] = (await github.issue_detail("octo-dev/lanternfish", 9)).comments
        assert comment.text == "ok"

        await pilot.press("e")  # the issue, not the saved filter
        await settled(pilot)
        assert isinstance(app.screen, EditForm)
        assert app.screen.issue.key == "octo-dev/lanternfish#9"
