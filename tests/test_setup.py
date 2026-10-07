from dataclasses import replace

from textual.widgets import Input, SelectionList, Static

from lazyissues import config as config_module
from lazyissues import demo
from lazyissues.config import Config, Preferences, Repo, Status
from lazyissues.discovery import STARTER_FILTERS
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import Issue
from lazyissues.setup import SetupApp

SIZE = (100, 50)


async def settle(pilot) -> None:
    """Let messages, then any workers they started, finish."""
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def click(pilot, selector: str) -> None:
    await pilot.click(selector)
    await settle(pilot)


async def focus_list(pilot, name: str | None = None) -> None:
    """Focus the screen's selection list, or the one named after repo `name`."""
    lists = pilot.app.screen.query(SelectionList)
    next(widget for widget in lists if name is None or widget.name == name).focus()
    await pilot.pause()


def options(app: SetupApp) -> list[tuple[str, bool]]:
    """The screen's first selection list, as (option, checked) pairs."""
    widget = app.screen.query_one(SelectionList)
    values = [widget.get_option_at_index(i).value for i in range(widget.option_count)]
    return [(value, value in widget.selected) for value in values]


async def test_setup_saves_the_edited_proposal_and_returns_it(tmp_path):
    path = tmp_path / "config.toml"
    app = SetupApp(demo.github(), path)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert options(app) == [("octo-dev/lanternfish", True), ("octo-dev/tidepool", True)]
        await click(pilot, "#next")

        # Tidepool is label-backed: check in-review and todo as statuses.
        await focus_list(pilot, "octo-dev/tidepool")
        await pilot.press("down", "down", "down", "down", "space", "down", "space")
        await click(pilot, "#next")

        assert options(app) == [
            ("Todo", False),
            ("In Progress", True),
            ("Blocked", False),
            ("in-review", False),
        ]
        await focus_list(pilot)
        await pilot.press("shift+down", "space")  # Todo after In Progress, and active
        await click(pilot, "#save")

    expected = Config(
        repos=[Repo("octo-dev/lanternfish", "project", "octo-dev/3"), Repo("octo-dev/tidepool")],
        statuses=[
            Status("In Progress", active=True),
            Status("Todo", active=True),
            Status("Blocked"),
            Status("in-review"),
        ],
        team=["octo-dev"],
        filters=list(STARTER_FILTERS),
    )
    assert app.return_value == expected
    assert config_module.load(path) == expected


async def test_setup_can_add_an_org_repo_and_drop_a_suggested_one(tmp_path):
    github = demo.github()
    github.labels["acme/web"] = ["ready"]
    app = SetupApp(github, tmp_path / "config.toml")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.click(Input)
        await pilot.press(*"acme/web", "enter")
        await settle(pilot)
        await focus_list(pilot)
        await pilot.press("space")  # drop lanternfish
        assert options(app) == [
            ("octo-dev/lanternfish", False),
            ("octo-dev/tidepool", True),
            ("acme/web", True),
        ]
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#save")
    assert app.return_value
    assert app.return_value.repo_names == ["octo-dev/tidepool", "acme/web"]


async def test_setup_reports_a_repo_it_cannot_find(tmp_path):
    app = SetupApp(demo.github(), tmp_path / "config.toml")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await pilot.click(Input)
        await pilot.press(*"acme/nope", "enter")
        await settle(pilot)
        assert "Could not resolve" in str(app.screen.query_one("#add-error", Static).render())
        assert len(options(app)) == 2


async def test_setup_explains_how_to_grant_the_project_scope(tmp_path):
    github = demo.github()
    github.scopes = {"repo"}
    app = SetupApp(github, tmp_path / "config.toml")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        warning = app.screen.query_one("#scope-warning", Static)
        assert "gh auth refresh -s project" in str(warning.render())


async def test_setup_accepts_an_empty_status_list(tmp_path):
    path = tmp_path / "config.toml"
    issue = Issue("o/r", 1, "An issue", "https://github.com/o/r/issues/1", ("me",))
    app = SetupApp(FakeGitHub(viewer="me", issues=[issue]), path)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#save")
    assert config_module.load(path).statuses == []


async def test_setup_can_retry_when_github_is_unreachable(tmp_path):
    class OfflineOnce(FakeGitHub):
        calls = 0

        async def whoami(self) -> tuple[str, set[str]]:
            self.calls += 1
            if self.calls == 1:
                raise GitHubError("Couldn't reach GitHub: offline")
            return await super().whoami()

    github = demo.github()
    app = SetupApp(OfflineOnce(github.viewer, github.issues, labels=github.labels), tmp_path / "c")
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        assert "offline" in str(app.query_one("#status", Static).render())
        await pilot.press("r")
        await settle(pilot)
        assert options(app) == [("octo-dev/lanternfish", True), ("octo-dev/tidepool", True)]


async def test_rerunning_setup_starts_from_the_config_and_keeps_the_rest(tmp_path):
    path = tmp_path / "config.toml"
    current = replace(
        demo.config(),
        repos=[Repo("octo-dev/tidepool")],
        statuses=[Status("Todo"), Status("In Progress", active=True, key="p")],
        done_window_days=7,
        pinned_milestones=["octo-dev/tidepool/v1.0"],
        preferences=Preferences(show_done=True, start_tab="Team", theme="nord"),
    )
    config_module.save(current, path)
    # Hand edits: a comment, and a setting only the file sets.
    text = path.read_text(encoding="utf-8")
    path.write_text(f"# my notes\ndone_window_days = 7\n{text}", encoding="utf-8")

    app = SetupApp(demo.github(), path, current)
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        # The config's repos are checked; other repos with my issues are offered.
        assert options(app) == [("octo-dev/tidepool", True), ("octo-dev/lanternfish", False)]
        await click(pilot, "#next")
        # Tidepool's labels that are config statuses start checked.
        assert options(app) == [
            ("bug", False),
            ("documentation", False),
            ("enhancement", False),
            ("in-progress", True),
            ("in-review", False),
            ("todo", True),
        ]
        await click(pilot, "#next")
        # The config's order, names, active statuses and keys.
        assert options(app) == [("Todo", False), ("In Progress", True)]
        assert "(p)" in str(app.screen.query_one(SelectionList).get_option_at_index(1).prompt)
        await click(pilot, "#save")

    assert app.return_value == current
    assert config_module.load(path) == current
    assert path.read_text(encoding="utf-8").startswith("# my notes\ndone_window_days = 7\n")


async def rerun_and_save(github: FakeGitHub, path) -> tuple[SetupApp, str]:
    """Rerun setup from the demo config, save unchanged, and return the Repos screen's text."""
    app = SetupApp(github, path, demo.config())
    async with app.run_test(size=SIZE) as pilot:
        await settle(pilot)
        repos_screen = " ".join(str(s.render()) for s in app.screen.query(Static))
        await click(pilot, "#next")
        await click(pilot, "#next")
        await click(pilot, "#save")
    return app, repos_screen


async def test_rerunning_setup_keeps_a_config_repo_github_would_not_read(tmp_path):
    class Locked(FakeGitHub):
        async def repo_labels(self, repo: str) -> list[str]:
            if repo == "octo-dev/lanternfish":
                raise GitHubError("Resource protected by organization SAML enforcement.")
            return await super().repo_labels(repo)

    github = demo.github()
    locked = Locked(github.viewer, github.issues, labels=github.labels, projects=github.projects)
    app, repos_screen = await rerun_and_save(locked, tmp_path / "config.toml")
    assert "Kept unchanged" in repos_screen
    assert "octo-dev/lanternfish: Resource protected" in repos_screen
    # Its project source, and the statuses only it may use, are saved as they were.
    assert app.return_value == demo.config()


async def test_rerunning_setup_without_the_project_scope_keeps_project_sources(tmp_path):
    github = demo.github()
    github.scopes = {"repo"}
    app, repos_screen = await rerun_and_save(github, tmp_path / "config.toml")
    assert "octo-dev/lanternfish" in repos_screen.split("Kept unchanged")[1]
    assert app.return_value == demo.config()
