"""Preferences (`S`) and keybinding help (`?`)."""

from dataclasses import replace

from textual.pilot import Pilot
from textual.widgets import (
    Checkbox,
    DataTable,
    Input,
    OptionList,
    Select,
    Static,
    TabbedContent,
    TextArea,
)

from lazyissues import config as config_module
from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.config import Preferences, SavedFilter, Status
from lazyissues.keys import KeysScreen
from lazyissues.move_picker import MovePicker
from lazyissues.preferences import PreferencesScreen
from lazyissues.status_list import StatusList


def firsts(app: LazyIssuesApp, view: str) -> list[str]:
    table = app.query_one(f"#{view} DataTable", DataTable)
    return [str(table.get_row_at(i)[1]) for i in range(table.row_count)]


def active_tab(app: LazyIssuesApp) -> str:
    tabs = app.query_one(TabbedContent)
    return tabs.get_tab(tabs.active).label_text


async def settled(pilot: Pilot) -> None:
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


async def test_the_app_starts_with_the_saved_theme_tab_and_done_visibility():
    preferences = Preferences(show_done=True, start_tab="Unassigned", theme="nord")
    app = LazyIssuesApp(replace(demo.config(), preferences=preferences), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        assert app.theme == "nord"
        assert active_tab(app) == "Unassigned"
        assert app.focused is app.query_one("#unassigned DataTable")
        assert firsts(app, "my-work")[-2:] == ["Done (1)", "tidepool#10"]


async def test_an_unknown_start_tab_starts_on_the_first_tab():
    preferences = Preferences(start_tab="Nope")
    app = LazyIssuesApp(replace(demo.config(), preferences=preferences), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        assert active_tab(app) == "My Work"


async def test_question_mark_lists_the_keys_of_the_app_lists_and_detail():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(100, 80)) as pilot:
        await settled(pilot)
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, KeysScreen)
        text = "\n".join(str(widget.render()) for widget in app.screen.query(Static))
        lines = {" ".join(line.split()) for line in text.splitlines()}
        assert {
            "Anywhere",
            "S Preferences",
            "? Keys",
            "q Quit",
            "Lists",
            "/ Search",
            "esc Clear search",  # hidden from the footer, still listed
            "F Focus previous status",
            "Issue detail",
            "← Previous",
            "h Activity",
            "p Move to In Progress (uppercase: at once)",  # from config
            "Move picker",
            "n New filter",
            "Preferences",
            "^s Save",
            "shift+↑ Move up",
        } <= lines
        await pilot.press("escape")
        assert not isinstance(app.screen, KeysScreen)


async def open_preferences(pilot: Pilot) -> PreferencesScreen:
    await pilot.press("S")
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, PreferencesScreen)
    return screen


async def highlight_theme(pilot: Pilot, theme: str) -> None:
    themes = pilot.app.screen.query_one("#theme", OptionList)
    themes.focus()
    themes.highlighted = themes.get_option_index(theme)
    for _ in range(100):  # the preview follows a message, which can lag on slow CI
        await pilot.pause()
        if pilot.app.theme == theme:
            return


async def test_saving_preferences_writes_config_and_applies_them_at_once(tmp_path):
    path = tmp_path / "config.toml"
    config_module.save(demo.config(), path)
    path.write_text("# my setup\n" + path.read_text())
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=path)
    async with app.run_test(size=(100, 60), notifications=True) as pilot:
        await settled(pilot)
        screen = await open_preferences(pilot)
        screen.query_one("#team", TextArea).load_text("sam-reef\nnew-dev\n")
        screen.query_one("#milestones", TextArea).load_text("octo-dev/tidepool/v0.4")
        screen.query_one("#show-done", Checkbox).value = True
        screen.query_one("#start-tab", Select).value = "Team"

        statuses = screen.query_one(StatusList)
        statuses.focus()
        await pilot.press("down", "down", "shift+up")  # In Review before In Progress
        await pilot.press("space")  # In Review is active
        screen.query_one("#key", Input).focus()
        await pilot.press("v")
        await highlight_theme(pilot, "nord")
        await pilot.press("ctrl+s")
        await settled(pilot)

        expected = replace(
            demo.config(),
            statuses=[
                Status("Todo"),
                Status("In Review", active=True, key="v"),
                Status("In Progress", active=True, key="p"),
            ],
            team=["sam-reef", "new-dev"],
            pinned_milestones=["octo-dev/tidepool/v0.4"],
            preferences=Preferences(show_done=True, start_tab="Team", theme="nord"),
        )
        assert not isinstance(app.screen, PreferencesScreen)
        assert app.config == expected
        assert config_module.load(path) == expected
        assert path.read_text().startswith("# my setup\n")
        assert app.theme == "nord"
        groups = [row for row in firsts(app, "my-work") if "(" in row]
        assert groups == [
            "No status (1)",
            "In Review (1)",
            "In Progress (2)",
            "Blocked (1)",
            "Done (1)",
        ]
        assert [row for row in firsts(app, "team") if "(" in row] == [
            "octo-dev (6)",
            "sam-reef (2)",
            "new-dev (0)",
        ]
        milestones = [row for row in firsts(app, "milestones") if "(" in row]
        assert [row.split("  ")[0] for row in milestones] == ["tidepool / v0.4 (3)"]  # done shown
        # The new key is a move shortcut at once.
        app.query_one("#my-work DataTable", DataTable).focus()
        await pilot.press("down", "v")
        await pilot.pause()  # not settled: the move waits on the picker
        assert isinstance(app.screen, MovePicker)
        await pilot.press("escape")


async def test_cancelling_preferences_reverts_the_previewed_theme_and_keeps_config(tmp_path):
    path = tmp_path / "config.toml"
    config_module.save(demo.config(), path)
    saved = path.read_text()
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=path)
    async with app.run_test(size=(100, 60), notifications=True) as pilot:
        await settled(pilot)
        screen = await open_preferences(pilot)
        screen.query_one("#team", TextArea).load_text("someone-else")
        await highlight_theme(pilot, "nord")
        assert app.theme == "nord"  # previewed while the highlight moves
        await highlight_theme(pilot, "gruvbox")
        assert app.theme == "gruvbox"
        await pilot.press("escape")
        await settled(pilot)

        assert not isinstance(app.screen, PreferencesScreen)
        assert app.theme == "textual-dark"
        assert app.config == demo.config()
        assert path.read_text() == saved


async def test_preferences_that_cannot_be_written_still_apply_and_say_so(tmp_path):
    path = tmp_path / "config.toml"
    path.mkdir()  # not writable as a file
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=path)
    async with app.run_test(size=(100, 60), notifications=True) as pilot:
        await settled(pilot)
        await open_preferences(pilot)
        await highlight_theme(pilot, "nord")
        await pilot.press("ctrl+s")
        await settled(pilot)
        assert app.config.preferences.theme == "nord"
        toasts = [str(toast.render()) for toast in app.screen.query("Toast")]
        assert any("Couldn't write" in toast for toast in toasts)


async def test_preferences_stay_open_until_pinned_milestones_and_keys_make_sense(tmp_path):
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=tmp_path / "config.toml")
    async with app.run_test(size=(100, 60)) as pilot:
        await settled(pilot)
        screen = await open_preferences(pilot)
        screen.query_one(StatusList).focus()
        screen.query_one("#key", Input).focus()
        await pilot.press("p")  # Todo can't take In Progress's key
        screen.query_one("#milestones", TextArea).load_text("v0.4")
        await pilot.press("ctrl+s")
        await settled(pilot)
        assert app.screen is screen
        assert screen.query_one(StatusList).statuses == demo.config().statuses
        assert not (tmp_path / "config.toml").exists()


async def test_a_status_key_the_lists_already_bind_is_warned_about(tmp_path):
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=tmp_path / "config.toml")
    async with app.run_test(size=(100, 60), notifications=True) as pilot:
        await settled(pilot)
        screen = await open_preferences(pilot)
        screen.query_one(StatusList).focus()
        screen.query_one("#key", Input).focus()
        await pilot.press("c")  # `c` creates an issue in the lists and the detail
        await pilot.pause()
        toasts = [str(toast.render()) for toast in app.screen.query("Toast")]
        assert any("c is already a key" in toast for toast in toasts)


async def test_saving_preferences_keeps_filters_saved_earlier(tmp_path):
    path = tmp_path / "config.toml"
    app = LazyIssuesApp(demo.config(), demo.github(), config_path=path)
    async with app.run_test(size=(100, 60)) as pilot:
        await settled(pilot)
        app.save_filters([SavedFilter("Bugs", "label:bug")])
        await open_preferences(pilot)
        await highlight_theme(pilot, "nord")
        await pilot.press("ctrl+s")
        await settled(pilot)
    saved = config_module.load(path)
    assert saved.filters == [SavedFilter("Bugs", "label:bug")]
    assert saved.preferences.theme == "nord"
