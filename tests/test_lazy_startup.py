"""Startup refreshes only the start tab; other tabs refresh the first time they are shown."""

from textual.widgets import TabbedContent

from lazyissues import demo
from lazyissues.app import LazyIssuesApp


async def settle(pilot) -> None:
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def test_startup_searches_only_the_start_tab_and_a_tab_searches_once_when_opened():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await settle(pilot)
        assert github.searched and all("assignee:@me" in q for q in github.searched)

        github.searched.clear()
        tabs = app.query_one(TabbedContent)
        tabs.active = "tab-3"  # Unassigned
        await settle(pilot)
        assert len(github.searched) == 1 and all("no:assignee" in q for q in github.searched)

        github.searched.clear()
        tabs.active = "tab-0"
        tabs.active = "tab-3"
        await settle(pilot)
        assert github.searched == []
