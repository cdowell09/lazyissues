"""The comment, assign, create and edit forms, driven from the list and the detail."""

import asyncio
import contextlib
import subprocess
import sys
from dataclasses import replace

from textual.widgets import DataTable, Input, Markdown, Select, Static, TextArea

from lazyissues.app import LazyIssuesApp
from lazyissues.config import Config, Repo, Status
from lazyissues.detail import IssueDetailScreen
from lazyissues.fake import FakeGitHub
from lazyissues.forms.form import Form, Picker
from lazyissues.github import GitHubError
from lazyissues.models import Issue, IssueDetail, Project
from lazyissues.mover import RejectedMoveBanner
from lazyissues.views.issue_list import IssueList


def issue(number: int, *labels: str, milestone: str | None = None) -> Issue:
    url = f"https://github.com/o/r/issues/{number}"
    return Issue("o/r", number, f"Issue {number}", url, ("me",), labels, milestone=milestone)


def github() -> FakeGitHub:
    """My Work lists: Todo (1), r#1, Doing (1), r#2. o/r keeps statuses in labels, o/p
    on project o/3."""
    return FakeGitHub(
        viewer="me",
        issues=[issue(1, "todo", milestone="v1"), issue(2, "doing")],
        details={"o/r#1": IssueDetail(issue(1), body="Old body")},
        labels={"o/r": ["bug", "doing", "todo"], "o/p": ["bug"]},
        milestones={"o/r": ["v1", "v2"], "o/p": ["Beta"]},
        assignable={"o/r": ["me", "sam", "kim"], "o/p": ["me"]},
        projects={"o/p": [Project("o/3", "Board", ("Todo", "Doing", "Done"))]},
    )


def app_on(gateway: FakeGitHub) -> LazyIssuesApp:
    config = Config(
        repos=[Repo("o/r"), Repo("o/p", "project", "o/3")],
        statuses=[Status("Todo"), Status("Doing")],
        team=["kim", "me"],
    )
    return LazyIssuesApp(config, gateway)


async def settle(pilot) -> None:
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def type_text(pilot, text: str) -> None:
    await pilot.press(*("space" if char == " " else char for char in text))


async def select_first_issue(pilot) -> None:
    await settle(pilot)
    await pilot.press("down")


async def test_c_comments_on_the_selected_issue_from_the_list():
    gateway = github()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)

        await pilot.press("C")
        await settle(pilot)
        assert isinstance(app.screen, Form)
        await type_text(pilot, "Looks good")
        await pilot.press("enter")
        await settle(pilot)

        assert not isinstance(app.screen, Form)
        [comment] = (await gateway.issue_detail("o/r", 1)).comments
        assert comment.text == "Looks good"
        assert app.details["o/r#1"].comments == (comment,)


async def test_shift_enter_and_ctrl_j_add_newlines_and_pasted_lines_stay():
    gateway = github()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("C")
        await settle(pilot)

        await pilot.press("a", "shift+enter", "b", "ctrl+j")
        app.screen.query_one(TextArea).insert("c\nd")  # what a paste inserts
        await pilot.press("enter")
        await settle(pilot)

        [comment] = (await gateway.issue_detail("o/r", 1)).comments
        assert comment.text == "a\nb\nc\nd"


class Recording(FakeGitHub):
    """Records searches; after `hold()`, answers the next detail fetch as GitHub had it
    when asked, but only once `ready` is set."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.searches: list[str] = []
        self.ready = asyncio.Event()
        self.holding = False

    def hold(self) -> None:
        self.ready.clear()
        self.holding = True

    async def search_issues(self, query: str) -> list[Issue]:
        self.searches.append(query)
        return await super().search_issues(query)

    async def issue_detail(self, repo: str, number: int) -> IssueDetail:
        detail = await super().issue_detail(repo, number)
        if self.holding:
            self.holding = False
            await self.ready.wait()
        return detail


def recording() -> Recording:
    fake = github()
    return Recording(
        viewer="me",
        issues=fake.issues,
        details=fake.details,
        labels=fake.labels,
        assignable=fake.assignable,
    )


async def test_a_comment_reloads_no_tab_and_an_assignment_reloads_the_others():
    gateway = recording()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        gateway.searches.clear()

        await pilot.press("C", "o", "k", "enter")
        await settle(pilot)
        assert gateway.searches == []  # a comment can't change which tabs list the issue

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("tab", "down", "space", "ctrl+s")  # unassign me
        await settle(pilot)
        assert gateway.searches  # tabs that don't list it may list it now
        assert not any("assignee:@me" in query for query in gateway.searches)  # My Work has it


async def test_a_detail_fetch_sent_before_a_comment_doesnt_hide_it():
    gateway = recording()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("enter", "right")
        await settle(pilot)
        gateway.hold()
        await pilot.press("left")  # back on r#1, its fetch held
        await pilot.pause()

        await pilot.press("C", "o", "k", "enter")
        await pilot.pause()
        await pilot.pause()
        gateway.ready.set()  # the fetch sent before the comment answers last
        await settle(pilot)

        assert [c.text for c in app.details["o/r#1"].comments] == ["ok"]


async def test_comment_from_the_detail_shows_it_there():
    app = app_on(github())
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("enter")
        await settle(pilot)

        await pilot.press("C")
        await settle(pilot)
        await type_text(pilot, "From the detail")
        await pilot.press("enter")
        await settle(pilot)

        assert isinstance(app.screen, IssueDetailScreen)
        assert "From the detail" in [md.source for md in app.screen.query(Markdown)]


async def test_an_empty_comment_is_not_sent_and_escape_cancels():
    gateway = github()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("C")
        await settle(pilot)

        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app.screen, Form)

        await pilot.press("escape")
        assert not isinstance(app.screen, Form)
        assert (await gateway.issue_detail("o/r", 1)).comments == ()


def list_row(app: LazyIssuesApp, key: str) -> list[str]:
    table = app.query_one("#my-work DataTable", DataTable)
    return [str(cell) for cell in table.get_row(key)[1:]]


async def test_a_assigns_from_the_roster_and_repo_assignees_keeping_picks_the_filter_hides():
    gateway = github()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)

        await pilot.press("a")
        await settle(pilot)
        picker = app.screen.query_one(Picker)
        assert (picker.shown, picker.selected) == (["kim", "me", "sam"], ["me"])

        await type_text(pilot, "sa")
        assert picker.shown == ["sam"]
        await pilot.press("tab", "space", "ctrl+s")
        await settle(pilot)

        assert not isinstance(app.screen, Form)
        assert (await gateway.issue_detail("o/r", 1)).issue.assignees == ("me", "sam")
        assert list_row(app, "o/r#1")[3] == "me, sam"


async def test_assign_from_the_detail_shows_the_new_assignees_there():
    app = app_on(github())
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("enter")
        await settle(pilot)

        await pilot.press("a")
        await settle(pilot)
        await pilot.press("tab", "space", "ctrl+s")  # kim, first in the roster
        await settle(pilot)

        assert isinstance(app.screen, IssueDetailScreen)
        assert "Assignees: me, kim" in str(app.screen.query_one(".fields", Static).render())


async def test_a_failed_write_shows_githubs_error_and_keeps_the_form():
    gateway = github()
    gateway.read_only.add("o/r")
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("a")
        await settle(pilot)

        await pilot.press("tab", "space", "ctrl+s")
        await settle(pilot)

        assert isinstance(app.screen, Form)
        assert "not accessible" in str(app.screen.query_one("#message", Static).render())
        assert (await gateway.issue_detail("o/r", 1)).issue.assignees == ("me",)


async def test_e_edits_the_title_and_sends_only_what_changed():
    gateway = github()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)

        await pilot.press("e")
        await settle(pilot)
        form = app.screen
        assert form.query_one("#title", Input).value == "Issue 1"
        assert form.query_one("#body", TextArea).text == "Old body"
        assert form.query_one("#labels", Picker).selected == []  # `todo` is its status
        assert form.query_one("#milestone", Select).value == "v1"

        gateway.details["o/r#1"] = IssueDetail(issue(1), body="Edited elsewhere")
        await pilot.press("ctrl+u")
        await type_text(pilot, "Renamed")
        await pilot.press("enter")
        await settle(pilot)

        assert not isinstance(app.screen, Form)
        detail = await gateway.issue_detail("o/r", 1)
        assert (detail.issue.title, detail.body) == ("Renamed", "Edited elsewhere")
        assert list_row(app, "o/r#1")[1] == "Renamed"


async def test_edit_adds_and_removes_labels_leaving_status_labels_and_others_changes():
    gateway = github()
    gateway.labels["o/r"] += ["docs", "ux"]
    gateway.issues[0] = replace(gateway.issues[0], labels=("todo", "ux"))
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("e")
        await settle(pilot)
        picker = app.screen.query_one("#labels", Picker)
        assert picker.shown == ["bug", "docs", "ux"]  # status labels change only by a move
        assert picker.selected == ["ux"]

        # Meanwhile someone adds `docs` on GitHub.
        gateway.issues[0] = replace(gateway.issues[0], labels=("todo", "ux", "docs"))
        app.screen.query_one("#milestone", Select).value = "v2"
        app.screen.query_one("#labels SelectionList").focus()
        await pilot.press("space", "down", "down", "space")  # pick bug, unpick ux
        await pilot.press("ctrl+s")
        await settle(pilot)

        detail = await gateway.issue_detail("o/r", 1)
        assert (set(detail.issue.labels), detail.issue.milestone) == ({"todo", "docs", "bug"}, "v2")
        assert app.details["o/r#1"] == detail


def fake_editor(tmp_path, code: str) -> str:
    script = tmp_path / "fake_editor.py"
    script.write_text(f"import sys, pathlib\npath = pathlib.Path(sys.argv[1])\n{code}\n")
    return subprocess.list2cmdline([sys.executable, str(script)])


async def test_ctrl_e_round_trips_the_body_through_the_editor_for_review(tmp_path, monkeypatch):
    edit = "path.write_text(path.read_text() + ' and more', encoding='utf-8')"
    monkeypatch.setenv("VISUAL", fake_editor(tmp_path, edit))
    gateway = github()
    app = app_on(gateway)
    monkeypatch.setattr(app, "suspend", contextlib.nullcontext)  # the test driver can't
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("enter")
        await settle(pilot)
        await pilot.press("e")
        await settle(pilot)

        await pilot.press("ctrl+e")  # the body, the form's text field, from the title
        await settle(pilot)
        assert isinstance(app.screen, Form)  # back for review, not sent
        assert app.screen.query_one("#body", TextArea).text == "Old body and more"
        assert (await gateway.issue_detail("o/r", 1)).body == "Old body"

        await pilot.press("enter")
        await settle(pilot)
        assert isinstance(app.screen, IssueDetailScreen)
        assert "Old body and more" in [md.source for md in app.screen.query(Markdown)]


async def test_a_failing_editor_keeps_the_text_and_says_why(tmp_path, monkeypatch):
    monkeypatch.setenv("VISUAL", fake_editor(tmp_path, "sys.exit(1)"))
    app = app_on(github())
    monkeypatch.setattr(app, "suspend", contextlib.nullcontext)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("C")
        await settle(pilot)
        await type_text(pilot, "Draft")

        await pilot.click("#editor")
        await settle(pilot)

        assert app.screen.query_one(TextArea).text == "Draft"
        assert "exited with 1" in str(app.screen.query_one("#message", Static).render())


def select(app: LazyIssuesApp, id: str) -> Select:
    return app.screen.query_one(f"#{id}", Select)


async def choose_project_repo(pilot) -> None:
    """Pick o/p in the create form and wait until its choices have loaded: the repo's
    `Changed` may only start the load after the app looks settled (slow Windows CI)."""
    select(pilot.app, "repo").value = "o/p"
    for _ in range(100):
        await settle(pilot)
        if options(pilot.app, "milestone") == ["Beta"]:  # o/p's only milestone
            return
    raise AssertionError("o/p's choices never loaded")


def options(app: LazyIssuesApp, id: str) -> list[str]:
    return [str(prompt) for prompt, _ in select(app, id)._options if _ is not Select.NULL]


async def test_c_creates_an_issue_with_its_status_label_in_a_label_backed_repo():
    gateway = github()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)

        await pilot.press("c")
        await settle(pilot)
        assert select(app, "repo").value == "o/r"  # the selected issue's repo
        assert options(app, "status") == ["Todo", "Doing"]
        app.screen.query_one("#title", Input).focus()
        await type_text(pilot, "Fresh issue")
        await pilot.press("tab")
        await type_text(pilot, "Body text")
        select(app, "milestone").value = "v2"
        select(app, "status").value = "Doing"
        await pilot.press("ctrl+s")
        await settle(pilot)

        assert not isinstance(app.screen, Form)
        created = await gateway.issue_detail("o/r", 3)
        assert (created.issue.title, created.body) == ("Fresh issue", "Body text")
        assert (created.issue.labels, created.issue.milestone) == (("doing",), "v2")
        cached = app.details["o/r#3"]
        assert (cached.body, cached.issue.milestone) == ("Body text", "v2")
        # Unassigned lists new issues, so it reloads to show this one.
        assert "o/r#3" in [i.key for i in app.query_one("#unassigned", IssueList).store.issues]


async def test_create_in_a_project_backed_repo_sets_its_status_on_the_project():
    gateway = github()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot)

        await choose_project_repo(pilot)
        assert options(app, "status") == ["Todo", "Doing"]  # Done is reached by closing
        assert options(app, "milestone") == ["Beta"]
        app.screen.query_one("#title", Input).value = "On the board"
        select(app, "status").value = "Doing"
        await pilot.press("ctrl+s")
        await settle(pilot)

        created = (await gateway.issue_detail("o/p", 1)).issue
        assert created.project_statuses == {"o/3": "Doing"}

        await pilot.press("c")  # the last repo used is the default now
        await settle(pilot)
        assert select(app, "repo").value == "o/p"


class Unscoped(FakeGitHub):
    """A token without the `project` scope: projects can't be read."""

    async def project_status_options(self, project: str) -> list[str]:
        raise GitHubError("Your token has not been granted the required scopes")


async def test_create_without_access_to_the_project_offers_no_status_but_still_creates():
    fake = github()
    gateway = Unscoped(viewer="me", labels=fake.labels, milestones=fake.milestones)
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot)
        await choose_project_repo(pilot)
        assert options(app, "status") == []

        app.screen.query_one("#title", Input).value = "Off the board"
        await pilot.press("ctrl+s")
        await settle(pilot)
        assert [issue.title for issue in gateway.issues] == ["Off the board"]


class BoardlessGitHub(FakeGitHub):
    async def set_project_status(self, repo, number, project, status):
        raise GitHubError("Resource not accessible by integration")


async def test_a_created_issue_whose_status_fails_is_still_created_and_the_form_closes():
    fake = github()
    gateway = BoardlessGitHub(
        viewer="me", labels=fake.labels, milestones=fake.milestones, projects=fake.projects
    )
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await settle(pilot)
        await pilot.press("c")
        await settle(pilot)
        await choose_project_repo(pilot)
        app.screen.query_one("#title", Input).value = "On the board"
        select(app, "status").value = "Doing"

        await pilot.press("ctrl+s")
        await settle(pilot)

        # Created, so the form is gone (retrying would create it twice), and the status
        # is a rejected move, shown until dismissed.
        assert isinstance(app.screen, RejectedMoveBanner)
        assert [issue.key for issue in gateway.issues] == ["o/p#1"]


async def test_create_needs_a_title_and_works_from_the_detail():
    gateway = github()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        await pilot.press("enter")
        await settle(pilot)

        await pilot.press("c")
        await settle(pilot)
        await pilot.press("ctrl+s")
        await settle(pilot)
        assert "title" in str(app.screen.query_one("#message", Static).render())

        app.screen.query_one("#title", Input).value = "From the detail"
        await pilot.press("ctrl+s")
        await settle(pilot)
        assert isinstance(app.screen, IssueDetailScreen)
        assert (await gateway.issue_detail("o/r", 3)).issue.title == "From the detail"


class CountingDetails(FakeGitHub):
    """Counts detail fetches."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.detail_fetches = 0

    async def issue_detail(self, repo: str, number: int) -> IssueDetail:
        self.detail_fetches += 1
        return await super().issue_detail(repo, number)


def counting() -> CountingDetails:
    fake = github()
    return CountingDetails(
        viewer="me",
        issues=fake.issues,
        details=fake.details,
        labels=fake.labels,
        milestones=fake.milestones,
        assignable=fake.assignable,
    )


async def test_assign_opens_without_a_detail_fetch_and_keeps_an_assignee_added_since_the_list():
    gateway = counting()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        gateway.detail_fetches = 0

        await pilot.press("a")
        await settle(pilot)
        assert gateway.detail_fetches == 0

        # Meanwhile someone assigns sam on GitHub; I add kim.
        gateway.issues[0] = replace(gateway.issues[0], assignees=("me", "sam"))
        await type_text(pilot, "kim")
        await pilot.press("tab", "space", "ctrl+s")
        await settle(pilot)

        assert (await gateway.issue_detail("o/r", 1)).issue.assignees == ("me", "sam", "kim")


async def test_edit_opens_from_the_detail_cache_when_it_holds_the_issue():
    gateway = counting()
    app = app_on(gateway)
    async with app.run_test() as pilot:
        await select_first_issue(pilot)
        app.details["o/r#1"] = IssueDetail(issue(1, "todo", milestone="v1"), body="Cached body")
        gateway.detail_fetches = 0

        await pilot.press("e")
        await settle(pilot)

        assert gateway.detail_fetches == 0
        assert app.screen.query_one("#body", TextArea).text == "Cached body"
