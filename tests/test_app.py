import sys

import pytest
from textual.widgets import DataTable

from lazyissues import __main__, demo
from lazyissues.app import LazyIssuesApp
from lazyissues.fake import FakeGitHub


def rows(app: LazyIssuesApp) -> list[list[str]]:
    table = app.query_one("#my-work DataTable", DataTable)
    return [[str(cell) for cell in table.get_row_at(i)] for i in range(table.row_count)]


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
            "lanternfish#11",
        ]
        assert rows(app)[4][2] == "octo-dev, sam-reef"


async def test_my_work_says_when_nothing_is_assigned():
    app = LazyIssuesApp(demo.config(), FakeGitHub(viewer="nobody"))
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        assert rows(app) == [["", "No open issues are assigned to you.", "", ""]]


def test_bad_config_exits_before_the_tui_starts(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(__main__.config_module, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["lazyissues"])
    with pytest.raises(SystemExit, match="No config"):
        __main__.main()


async def test_my_work_fills_the_space_under_the_tabs():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        # Header, tab bar and footer take 4 rows; My Work gets the rest.
        assert app.query_one("#my-work").region.height == 20
