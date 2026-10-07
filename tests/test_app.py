import sys

import pytest
from listed import plain
from textual.widgets import DataTable, Input, TabbedContent

from lazyissues import __main__, demo
from lazyissues.app import LazyIssuesApp
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError


def rows(app: LazyIssuesApp) -> list[list[str]]:
    table = app.query_one("#my-work DataTable", DataTable)
    return [[ref, *map(str, table.get_row_at(i)[2:])] for i, ref in enumerate(plain(table))]


async def test_my_work_groups_my_open_issues_by_status_across_the_repo_set():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        assert [row[0] for row in rows(app)] == [
            "No status (1)",
            "lanternfish#9",
            # A label-backed `in-progress` and a project's "In Progress" are one group.
            "In Progress (2)",
            "tidepool#12",
            "lanternfish#4",
            "In Review (1)",
            "tidepool#15 ⚠",  # two status labels
            "Blocked (1)",  # not a configured status, so after the configured ones
            "lanternfish#9 → lanternfish#11",  # a sub-issue whose parent is in another group
        ]
        assert rows(app)[4][2:4] == ["In Progress", "octo-dev, sam-reef"]


async def test_my_work_says_when_nothing_is_assigned():
    app = LazyIssuesApp(demo.config(), FakeGitHub(viewer="nobody"))
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        assert rows(app) == [["", "No open issues are assigned to you.", "", "", "", ""]]


@pytest.fixture
def launch(monkeypatch, tmp_path):
    """Run `main()` against a config dir in `tmp_path`, a fake GitHub, and stub apps.

    Returns the configs each app was started with, as ("setup" | "app", config) pairs.
    """
    started: list[tuple[str, object]] = []
    monkeypatch.setattr(sys, "argv", ["lazyissues"])
    monkeypatch.setattr(__main__.config_module, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(__main__, "gh_token", lambda: "token")
    monkeypatch.setattr(__main__, "cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(__main__, "GraphQLGateway", lambda token: demo.github())

    def run_setup(self):
        started.append(("setup", self.path))
        return demo.config()

    monkeypatch.setattr(__main__.SetupApp, "run", run_setup)
    monkeypatch.setattr(
        __main__.LazyIssuesApp, "run", lambda self: started.append(("app", self.config))
    )
    return started


def test_bad_config_exits_before_the_tui_starts(launch, tmp_path):
    (tmp_path / "config.toml").write_text("repos = [")
    with pytest.raises(SystemExit, match="not valid TOML"):
        __main__.main()
    assert launch == []


def test_missing_gh_exits_before_the_tui_starts(launch, monkeypatch):
    def no_gh():
        raise GitHubError("`gh` isn't logged in. Run `gh auth login`.")

    monkeypatch.setattr(__main__, "gh_token", no_gh)
    with pytest.raises(SystemExit, match="gh auth login"):
        __main__.main()
    assert launch == []


def test_without_a_config_setup_runs_then_the_app_starts_with_its_config(launch, tmp_path):
    __main__.main()
    assert launch == [("setup", tmp_path / "config.toml"), ("app", demo.config())]


def test_the_app_saves_to_the_config_it_was_started_with(launch, monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    __main__.config_module.save(demo.config(), path)
    monkeypatch.setattr(
        __main__.LazyIssuesApp, "run", lambda self: launch.append(("app", self.config_path))
    )
    __main__.main()
    assert launch == [("app", path)]


def test_setup_flag_reruns_setup_from_the_current_config(launch, monkeypatch, tmp_path):
    path = tmp_path / "config.toml"
    __main__.config_module.save(demo.config(), path)
    monkeypatch.setattr(sys, "argv", ["lazyissues", "--setup"])
    monkeypatch.setattr(
        __main__.SetupApp, "run", lambda self: launch.append(("setup", self.current)) or None
    )
    with pytest.raises(SystemExit, match=r"config\.toml is unchanged"):
        __main__.main()
    assert launch == [("setup", demo.config())]


def test_setup_flag_without_a_config_runs_first_run_setup(launch, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["lazyissues", "--setup"])
    __main__.main()
    assert launch == [("setup", tmp_path / "config.toml"), ("app", demo.config())]


def test_quitting_setup_starts_nothing(launch, monkeypatch):
    monkeypatch.setattr(__main__.SetupApp, "run", lambda self: None)
    with pytest.raises(SystemExit, match="setup"):
        __main__.main()
    assert launch == []


async def test_my_work_fills_the_space_under_the_tabs():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        # Header, tab bar and footer take 4 rows; My Work gets the rest.
        assert app.query_one("#my-work").region.height == 20


def active_tab(app: LazyIssuesApp) -> str:
    tabs = app.query_one(TabbedContent)
    return tabs.get_tab(tabs.active).label_text


async def test_number_keys_jump_to_a_tab_and_focus_its_list_from_every_tab():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        for key, tab, focused in [
            ("2", "Team", "#team DataTable"),
            ("5", "Filters", "#filters FilterResults DataTable"),  # where tab swaps panes
            ("3", "Milestones", "#milestones DataTable"),
            ("5", "Filters", "#filters FilterResults DataTable"),
            ("1", "My Work", "#my-work DataTable"),
        ]:
            await pilot.press(key)
            await pilot.pause()
            assert (active_tab(app), app.focused) == (tab, app.query_one(focused))


async def test_brackets_step_to_the_previous_and_next_tab_wrapping_around():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        seen = []
        for key in ["left_square_bracket", "left_square_bracket", "right_square_bracket"]:
            await pilot.press(key)
            await pilot.pause()
            seen.append(active_tab(app))
        assert seen == ["Filters", "Unassigned", "Filters"]
        await pilot.press("right_square_bracket")
        await pilot.pause()
        assert (active_tab(app), app.focused) == ("My Work", app.query_one("#my-work DataTable"))


async def test_a_number_key_types_into_the_search_box_instead_of_switching_tabs():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.press("slash", "2")
        assert active_tab(app) == "My Work"
        assert app.query_one("#my-work #search", Input).value == "2"
