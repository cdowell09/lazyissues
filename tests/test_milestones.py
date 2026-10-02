"""The Milestones tab: each repo's open milestones with progress, issues by status."""

from dataclasses import replace

from textual.widgets import DataTable

from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import Milestone
from lazyissues.view_model import ViewState
from lazyissues.views.milestones import Milestones


def firsts(app: LazyIssuesApp) -> list[str]:
    table = app.query_one("#milestones DataTable", DataTable)
    return [str(table.get_row_at(i)[0]) for i in range(table.row_count)]


async def shown(app: LazyIssuesApp) -> list[str]:
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        return firsts(app)


async def test_lists_each_repos_milestones_with_progress_and_issues_by_status():
    assert await shown(LazyIssuesApp(demo.config(), demo.github())) == [
        "tidepool / v0.4 (2)  ███░░░░░░░ 1/3",  # done counts every closed issue in it
        "tidepool#12",
        "tidepool#15 ⚠",
        "tidepool / v1.0 (1)  ░░░░░░░░░░ 0/1",
        "tidepool#18",
        "lanternfish / v1.0 (4)  ██░░░░░░░░ 1/5",  # same title, another repo
        "lanternfish#9",
        "└ lanternfish#7",
        "└ lanternfish#11",
        "lanternfish#4",
    ]


async def test_pinned_milestones_limit_and_order_the_tab():
    config = replace(
        demo.config(),
        pinned_milestones=["octo-dev/lanternfish/v1.0", "octo-dev/tidepool/v0.4"],
    )
    found = await shown(LazyIssuesApp(config, demo.github()))
    assert [row for row in found if " / " in row] == [
        "lanternfish / v1.0 (4)  ██░░░░░░░░ 1/5",
        "tidepool / v0.4 (2)  ███░░░░░░░ 1/3",
    ]


async def test_says_when_the_repo_set_has_no_open_milestones():
    app = LazyIssuesApp(demo.config(), replace(demo.github(), milestones={}))
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        table = app.query_one("#milestones DataTable", DataTable)
        assert [str(cell) for cell in table.get_row_at(0)][:2] == [
            "",
            "No open milestones in the repo set.",
        ]
        assert table.row_count == 1


async def test_repo_filter_hides_other_repos_milestones():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await pilot.app.workers.wait_for_complete()
        app.query_one(Milestones).state = ViewState(repo="octo-dev/lanternfish")
        await pilot.pause()
        assert [row for row in firsts(app) if " / " in row] == [
            "lanternfish / v1.0 (4)  ██░░░░░░░░ 1/5"
        ]


class NoMilestones(FakeGitHub):
    async def repo_milestones(self, repo: str) -> list[Milestone]:
        raise GitHubError("Couldn't reach GitHub: timed out")


async def test_opens_from_the_snapshot_before_github_answers(tmp_path):
    await shown(LazyIssuesApp(demo.config(), demo.github(), cache=tmp_path))
    offline = NoMilestones(**vars(demo.github()))
    found = await shown(LazyIssuesApp(demo.config(), offline, cache=tmp_path))
    # The snapshot's milestones, without progress until GitHub answers.
    assert [row for row in found if " / " in row] == [
        "tidepool / v0.4 (2)",
        "tidepool / v1.0 (1)",
        "lanternfish / v1.0 (4)",
    ]
