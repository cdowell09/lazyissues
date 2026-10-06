"""Startup refreshes only the start tab; other tabs refresh the first time they are shown."""

from listed import show_tab
from textual.widgets import TabbedContent

from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.views.team import Team
from lazyissues.views.unassigned import Unassigned


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


async def test_a_reload_of_a_tab_never_shown_waits_for_its_first_show():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await settle(pilot)
        team = app.query_one(Team)
        github.searched.clear()

        team.reload()  # as a write that regroups does to every tab
        await settle(pilot)
        assert github.searched == []

        await show_tab(pilot, "team")
        assert github.searched and len(github.searched) == len(set(github.searched))


async def test_a_refresh_that_finishes_while_a_tab_is_hidden_redraws_it_when_shown(monkeypatch):
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        tabs = app.query_one(TabbedContent)
        tabs.active = "tab-3"  # Unassigned, loaded now
        await settle(pilot)
        tabs.active = "tab-0"
        await settle(pilot)
        unassigned = app.query_one(Unassigned)
        drawn: list[None] = []
        real_show = unassigned.show

        def counting_show() -> None:
            drawn.append(None)
            real_show()

        monkeypatch.setattr(unassigned, "show", counting_show)

        unassigned.reload()
        await settle(pilot)
        assert drawn == []  # hidden: not drawn

        tabs.active = "tab-3"
        await settle(pilot)
        assert len(drawn) == 1


async def test_applying_a_config_to_a_hidden_tab_redraws_it_only_when_shown(monkeypatch):
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settle(pilot)
        team = app.query_one(Team)  # never shown
        drawn: list[None] = []
        real_show = team.show

        def counting_show() -> None:
            drawn.append(None)
            real_show()

        monkeypatch.setattr(team, "show", counting_show)

        team.configure(app.config)
        assert drawn == []

        await show_tab(pilot, "team")
        assert len(drawn) == 2  # the pending redraw, then the first refresh's result
