import asyncio
import webbrowser
from dataclasses import replace
from datetime import UTC, datetime

from textual.widgets import DataTable, Markdown, Static

from lazyissues.app import LazyIssuesApp
from lazyissues.config import Config, Repo, Status
from lazyissues.detail import IssueDetailScreen
from lazyissues.fake import FakeGitHub
from lazyissues.models import Event, Issue, IssueDetail, ProjectField

AT = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def issue(number: int, *labels: str, milestone: str | None = None) -> Issue:
    url = f"https://github.com/o/r/issues/{number}"
    return Issue("o/r", number, f"Issue {number}", url, ("me",), labels, milestone=milestone)


def github() -> FakeGitHub:
    """My Work shows: Todo (1), r#1, Doing (2), r#2, r#3."""
    return FakeGitHub(
        viewer="me",
        issues=[issue(1, "todo", milestone="v1"), issue(2, "doing"), issue(3, "doing")],
        details={
            "o/r#1": IssueDetail(
                issue(1),
                body="Steps to **reproduce**",
                parent=issue(9),
                sub_issues=(issue(2),),
                project_fields=(ProjectField("Roadmap", "Theme", "Reliability"),),
                activity=(
                    Event("sam", AT, "commented", "First comment"),
                    Event("me", AT, "labeled", "bug"),
                    Event("kim", AT, "commented", "Second comment"),
                ),
            )
        },
    )


def app_on(gateway: FakeGitHub) -> LazyIssuesApp:
    config = Config(repos=[Repo("o/r")], statuses=[Status("Todo"), Status("Doing")])
    return LazyIssuesApp(config, gateway)


def markdown(app: LazyIssuesApp) -> list[str | None]:
    return [widget.source for widget in app.screen.query(Markdown)]


def text(app: LazyIssuesApp) -> str:
    """Everything the screen shows, Markdown included."""
    return "\n".join(str(widget.render()) for widget in app.screen.query(Static))


async def open_detail(pilot) -> None:
    """Opens the first issue, under the first group's header row."""
    await pilot.app.workers.wait_for_complete()
    await pilot.press("down", "enter")
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def test_enter_opens_the_detail_with_markdown_body_and_comments_oldest_first():
    app = app_on(github())
    async with app.run_test() as pilot:
        await open_detail(pilot)

        assert markdown(app) == ["Steps to **reproduce**", "First comment", "Second comment"]
        shown = text(app)
        for expected in ("Issue 1", "v1", "r#9", "Issue 9", "Issue 2", "Theme", "Reliability"):
            assert expected in shown


class SlowGitHub(FakeGitHub):
    """Answers detail fetches only once `ready` is set."""

    def __init__(self) -> None:
        fake = github()
        super().__init__(viewer=fake.viewer, issues=fake.issues, details=fake.details)
        self.ready = asyncio.Event()
        self.ready.set()

    async def issue_detail(self, repo: str, number: int) -> IssueDetail:
        await self.ready.wait()
        return await super().issue_detail(repo, number)


async def test_reopening_shows_the_cached_detail_then_the_latest_from_github():
    gateway = SlowGitHub()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await open_detail(pilot)
        await pilot.press("escape")
        gateway.details["o/r#1"] = replace(gateway.details["o/r#1"], body="Edited on GitHub")
        gateway.ready.clear()

        await pilot.press("enter")
        await pilot.pause()
        assert markdown(app)[0] == "Steps to **reproduce**"

        gateway.ready.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert markdown(app)[0] == "Edited on GitHub"


async def test_an_uncached_detail_shows_the_list_copy_while_loading():
    gateway = SlowGitHub()
    gateway.ready.clear()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert "Issue 1" in text(app)
        assert "Loading…" in text(app)
        gateway.ready.set()


def list_cursor(app: LazyIssuesApp) -> int:
    return app.query_one("#my-work DataTable", DataTable).cursor_row


def title(app: LazyIssuesApp) -> str:
    return str(app.screen.query_one(".title", Static).content)


async def test_enter_on_a_group_header_opens_nothing():
    app = app_on(github())
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(app.screen, IssueDetailScreen)


async def test_arrows_step_through_the_issues_over_group_headers_and_move_the_selection():
    app = app_on(github())
    async with app.run_test() as pilot:
        await open_detail(pilot)

        await pilot.press("right")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert (title(app), list_cursor(app)) == ("r#2  Issue 2", 3)

        await pilot.press("right", "right")  # the last press is past the end
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert (title(app), list_cursor(app)) == ("r#3  Issue 3", 4)

        await pilot.press("left", "left", "left")  # the last press is before the start
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert (title(app), list_cursor(app)) == ("r#1  Issue 1", 1)

        await pilot.press("escape")
        assert not isinstance(app.screen, IssueDetailScreen)
        assert list_cursor(app) == 1


async def test_z_toggles_full_screen():
    app = app_on(github())
    async with app.run_test(size=(100, 30)) as pilot:
        await open_detail(pilot)

        def width() -> int:
            return app.screen.query_one("#detail").outer_size.width

        assert width() < 100
        await pilot.press("z")
        assert width() == 100
        await pilot.press("z")
        assert width() < 100


async def test_o_opens_the_issue_in_the_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    app = app_on(github())
    async with app.run_test() as pilot:
        await open_detail(pilot)
        await pilot.press("right", "o")
        assert opened == ["https://github.com/o/r/issues/2"]


async def test_h_shows_the_activity_timeline_and_back():
    app = app_on(github())
    async with app.run_test() as pilot:
        await open_detail(pilot)

        await pilot.press("h")
        await pilot.pause()
        shown = text(app)
        assert "sam commented" in shown
        assert "me added label bug" in shown
        assert shown.index("First comment") < shown.index("added label") < shown.index("kim")
        assert "Theme" not in shown

        await pilot.press("h")
        await pilot.pause()
        assert "Theme" in text(app)


async def test_a_failed_fetch_is_reported_and_the_detail_stays_open():
    gateway = github()
    gateway.issues.append(issue(4))
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        gateway.issues.remove(issue(4))  # deleted on GitHub after the list loaded
        await open_detail(pilot)  # r#4 has no status, so it comes first

        assert isinstance(app.screen, IssueDetailScreen)
        assert "Issue 4" in text(app)
        assert "Couldn't load r#4" in text(app)


def long_thread() -> FakeGitHub:
    fake = github()
    for number in (1, 2):
        events = tuple(Event("sam", AT, "commented", f"#{number} comment {n}") for n in range(60))
        fake.details[f"o/r#{number}"] = IssueDetail(issue(number), body="Body", activity=events)
    return fake


async def test_a_long_thread_fills_in_completely_and_keeps_links():
    app = app_on(long_thread())
    async with app.run_test() as pilot:
        await open_detail(pilot)
        await app.workers.wait_for_complete()
        await pilot.pause()

        sources = markdown(app)
        assert sources == ["Body", *(f"#1 comment {n}" for n in range(60))]
        assert len(app.screen.query(Markdown)) == 61


async def test_stepping_mid_fill_shows_only_the_new_issue():
    app = app_on(long_thread())
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.press("down", "enter", "right")  # no waiting for the fill
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert markdown(app) == ["Body", *(f"#2 comment {n}" for n in range(60))]


def detail_children(app: LazyIssuesApp) -> list:
    return list(app.screen.query_one("#detail").children)


async def test_a_change_to_another_issue_leaves_the_detail_alone():
    app = app_on(github())
    async with app.run_test() as pilot:
        await open_detail(pilot)
        before = detail_children(app)

        app.mover.changed.publish(issue(3, "todo"))
        await pilot.pause()

        assert detail_children(app) == before


async def test_a_refetch_that_finds_nothing_new_leaves_the_detail_alone():
    app = app_on(github())
    async with app.run_test() as pilot:
        await open_detail(pilot)
        before = detail_children(app)

        screen = app.screen
        assert isinstance(screen, IssueDetailScreen)
        await screen.fetch(screen.issue)
        await pilot.pause()

        assert detail_children(app) == before
