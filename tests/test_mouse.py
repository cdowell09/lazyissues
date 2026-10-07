"""The mouse: clicking, wheel scrolling, and selecting text to copy."""

from collections.abc import Callable

from textual import events
from textual.app import App
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import DataTable, Input, OptionList, SelectionList, Static, Tab, TabbedContent

from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.config import SavedFilter
from lazyissues.detail import IssueDetailScreen
from lazyissues.keys import KeysScreen
from lazyissues.models import CloseReason
from lazyissues.move_picker import MovePicker
from lazyissues.move_planner import Close
from lazyissues.preferences import PreferencesScreen
from lazyissues.setup import SetupApp, SourcesScreen
from lazyissues.status_list import StatusList
from lazyissues.views.filters import Filters
from lazyissues.views.issue_list import FOLDED, INDENT, UNFOLDED


async def until(pilot: Pilot, condition: Callable[[], bool]) -> None:
    """Let the app run until `condition` holds. (Waiting for every worker would wait on an
    open picker forever.)"""
    for _ in range(200):
        if condition():
            return
        await pilot.pause(0.01)
    raise AssertionError("timed out")


async def settled(pilot: Pilot) -> None:
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def table(app: App, view: str = "my-work") -> DataTable:
    return app.query_one(f"#{view} DataTable", DataTable)


def first_cell(app: App, row: int, view: str = "my-work") -> str:
    return str(table(app, view).get_row_at(row)[1])


# Each row starts with its checkbox (the first column: "☐" between paddings); a cell's
# text starts one column into the cell, after its padding.
ISSUE_TEXT = 4


async def click_row(pilot: Pilot, row: int, x: int = ISSUE_TEXT + 1, view: str = "my-work") -> None:
    """Click `row` of `view`'s list (0 is the first row under the column headers)."""
    await pilot.click(table(pilot.app, view), offset=(x, row + 1))
    await pilot.pause()


async def click_option(pilot: Pilot, options: OptionList, index: int) -> None:
    """Click the `index`th option of a list of one-line options, scrolled into view.

    An OptionList scrolls back to its highlight whenever it's resized, which a slow
    machine can do between this scrolling and clicking, so the click lands on another
    option. Click again until the option is the highlighted one.
    """
    for _ in range(20):
        options.scroll_to(y=index, animate=False, immediate=True)
        top = options.content_region.y - options.region.y
        await pilot.click(options, offset=(2, top + index - round(options.scroll_y)))
        await pilot.pause()
        if options.highlighted == index:
            return
    raise AssertionError(f"option {index} never took a click")


async def terminal(
    pilot: Pilot, event: type[events.MouseEvent], widget: Widget, offset: tuple[int, int]
) -> None:
    """Send a mouse event at `offset` in `widget` as the terminal would, so the app does
    what it does with real input (Pilot's clicks skip that: a release never becomes a click).
    """
    x, y = widget.region.offset + offset
    button = 0 if issubclass(event, events.MouseScrollDown) else 1
    pilot.app.post_message(event(None, x, y, 0, 1, button, False, False, False, x, y))
    await pilot.pause()


async def wheel(pilot: Pilot, widget: Widget, notches: int = 3) -> None:
    """Turn the mouse wheel down over the middle of `widget`."""
    middle = (widget.size.width // 2, widget.size.height // 2)
    for _ in range(notches):
        await terminal(pilot, events.MouseScrollDown, widget, middle)


async def quick_click(pilot: Pilot, widget: Widget) -> None:
    """Click `widget` as a terminal sends a quick click: the press and release together,
    so the app handles the release before anything the press set off."""
    x, y = widget.region.offset
    for event in (events.MouseDown, events.MouseUp):
        pilot.app.post_message(event(None, x, y, 0, 0, 1, False, False, False, x, y))
    await pilot.pause()


async def drag(pilot: Pilot, widget: Widget, start: tuple[int, int], end: tuple[int, int]) -> None:
    """Press the mouse at `start` in `widget`, move it to `end` and release it there."""
    await terminal(pilot, events.MouseDown, widget, start)
    await terminal(pilot, events.MouseMove, widget, end)
    await terminal(pilot, events.MouseUp, widget, end)


async def open_detail(pilot: Pilot) -> IssueDetailScreen:
    """Open My Work's first issue with two clicks."""
    await click_row(pilot, 1)
    await click_row(pilot, 1)
    await settled(pilot)
    assert isinstance(pilot.app.screen, IssueDetailScreen)
    return pilot.app.screen


async def click_tab(pilot: Pilot, label: str) -> None:
    await pilot.click(next(tab for tab in pilot.app.query(Tab) if str(tab.label) == label))
    await pilot.pause()


def copy_key(app: App) -> Widget | None:
    """The footer's Copy, if it shows one."""
    keys = app.screen.query("Footer FooterKey")
    return next((key for key in keys if str(key.render()).endswith(" Copy ")), None)


async def test_clicking_a_row_selects_it_and_clicking_it_again_opens_it():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await click_row(pilot, 1)
        assert table(app).cursor_row == 1
        assert not isinstance(app.screen, IssueDetailScreen)

        await click_row(pilot, 1, x=30)  # anywhere on the selected row
        assert isinstance(app.screen, IssueDetailScreen)
        assert app.screen.issue.ref == first_cell(app, 1).lstrip()


async def test_clicking_a_group_header_folds_and_unfolds_it():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        header = first_cell(app, 0)
        assert header.startswith(UNFOLDED)
        await click_row(pilot, 1)  # an issue, so the header isn't selected
        await click_row(pilot, 0)
        assert first_cell(app, 0) == header.replace(UNFOLDED, FOLDED, 1)
        await click_row(pilot, 0)
        assert first_cell(app, 0) == header


async def test_clicking_a_parents_fold_arrow_folds_and_unfolds_its_sub_issues():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await click_tab(pilot, "Team")
        assert first_cell(app, 4, "team") == "  ▾ lanternfish#9"
        assert first_cell(app, 5, "team") == "    └ lanternfish#11"

        arrow = ISSUE_TEXT + len(INDENT)  # under its group's name
        await click_row(pilot, 4, x=arrow, view="team")
        assert first_cell(app, 4, "team") == "  ▸ lanternfish#9"
        assert first_cell(app, 5, "team") == "▾ sam-reef (2)"
        await click_row(pilot, 4, x=arrow, view="team")
        assert first_cell(app, 5, "team") == "    └ lanternfish#11"
        assert not isinstance(app.screen, IssueDetailScreen)


async def test_clicking_a_tab_shows_it_and_its_rows_are_clickable():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await click_tab(pilot, "Team")
        assert app.query_one(TabbedContent).active_pane is app.query_one("#team").parent

        await click_row(pilot, 1, view="team")
        await click_row(pilot, 1, view="team")
        assert isinstance(app.screen, IssueDetailScreen)
        assert app.screen.issue.ref == first_cell(app, 1, "team").lstrip()


async def test_clicking_a_move_option_chooses_it_and_clicking_it_again_takes_it():
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github)
    async with app.run_test() as pilot:
        await settled(pilot)
        await click_row(pilot, 1)
        key = table(app).ordered_rows[1].key.value
        await pilot.press("m")
        await until(pilot, lambda: isinstance(app.screen, MovePicker))
        picker = app.screen
        assert isinstance(picker, MovePicker)
        options = picker.query_one(OptionList)
        completed = picker.targets.index(Close(CloseReason.COMPLETED))

        await click_option(pilot, options, completed)
        assert options.highlighted == completed
        assert app.screen is picker

        await click_option(pilot, options, completed)
        await settled(pilot)
        assert app.screen is not picker
        assert key in github.closed


async def test_clicking_the_close_control_closes_the_detail():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        detail = await open_detail(pilot)
        close = detail.query_one("#close")
        assert "[×]" in str(close.render())

        await pilot.click(close)
        assert app.screen is not detail


async def test_the_wheel_scrolls_lists_and_details():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(80, 10)) as pilot:
        await settled(pilot)
        detail = (await open_detail(pilot)).query_one("#detail")
        await wheel(pilot, detail)
        assert detail.scroll_y > 0

        await pilot.press("escape")
        assert table(app).scroll_y == 0
        await wheel(pilot, table(app))
        assert table(app).scroll_y > 0


async def test_dragging_across_detail_text_and_ctrl_c_copies_it():
    copied: list[str] = []
    app = LazyIssuesApp(demo.config(), demo.github(), system_clipboard=copied.append)
    async with app.run_test() as pilot:
        await settled(pilot)
        detail = await open_detail(pilot)
        title = detail.query_one("#detail .title")
        ref = detail.issue.ref
        await drag(pilot, title, (0, 0), (len(ref) - 1, 0))  # to its last character
        await pilot.press("ctrl+c")
        assert copied == [ref]
        assert app.clipboard == ref  # and OSC 52, Textual's own copy


async def test_dragging_across_a_list_row_selects_its_text_without_opening_it():
    copied: list[str] = []
    app = LazyIssuesApp(demo.config(), demo.github(), system_clipboard=copied.append)
    async with app.run_test(size=(120, 24)) as pilot:  # wide enough not to cut refs
        await settled(pilot)
        await click_row(pilot, 1)
        cell = first_cell(app, 1)
        ref = cell.lstrip()
        start = ISSUE_TEXT + len(cell) - len(ref)  # past the row's indentation
        # Row 1 is under the headers.
        await drag(pilot, table(app), (start, 2), (start + len(ref) - 1, 2))
        assert not isinstance(app.screen, IssueDetailScreen)
        await pilot.press("ctrl+c")
        assert copied == [ref]


async def test_the_footer_offers_copy_while_text_is_selected():
    copied: list[str] = []
    app = LazyIssuesApp(demo.config(), demo.github(), system_clipboard=copied.append)
    async with app.run_test(size=(120, 30)) as pilot:
        await settled(pilot)
        detail = await open_detail(pilot)
        assert copy_key(app) is None

        title = detail.query_one("#detail .title")
        ref = detail.issue.ref
        await drag(pilot, title, (0, 0), (len(ref) - 1, 0))
        await until(pilot, lambda: copy_key(app) is not None)  # the footer redraws on a refresh
        key = copy_key(app)
        assert key is not None
        await quick_click(pilot, key)
        await until(pilot, lambda: bool(copied))  # Copy presses ctrl+c, a message later
        assert copied == [ref]


async def test_the_list_footer_fits_80_columns_even_while_offering_copy():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot)
        cell = first_cell(app, 1)
        start = ISSUE_TEXT + len(cell) - len(cell.lstrip())
        await drag(pilot, table(app), (start, 2), (start + 3, 2))  # the footer's widest
        await until(pilot, lambda: copy_key(app) is not None)
        *keys, palette = app.screen.query("Footer FooterKey")  # ^p, docked on the right
        assert [" ".join(str(key.render()).split()) for key in keys] == [
            "o Open link",
            "/ Search",
            "m Move",
            "^c Copy",
            "q Quit",
            "r Refresh",
            "? Keys",
        ]
        assert keys[-1].region.right <= palette.region.x


def footer(app: App) -> tuple[list[str], int]:
    """The footer's keys as shown, and the column right of the last one."""
    keys = list(app.screen.query("Footer FooterKey"))
    return [" ".join(str(key.render()).split()) for key in keys], keys[-1].region.right


async def test_the_detail_footer_fits_80_columns_with_and_without_copy():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(80, 24)) as pilot:
        await settled(pilot)
        detail = await open_detail(pilot)
        keys = ["o Open link", "w Full screen", "h Activity", "m Move", "esc Close"]
        assert footer(app) == (keys, 59)

        await drag(pilot, detail.query_one("#detail .title"), (0, 0), (3, 0))
        await until(pilot, lambda: copy_key(app) is not None)
        assert footer(app) == (["^c Copy", *keys], 68)  # within 80 columns


async def test_dragging_across_a_form_field_and_ctrl_c_copies_it():
    copied: list[str] = []
    app = LazyIssuesApp(demo.config(), demo.github(), system_clipboard=copied.append)
    async with app.run_test() as pilot:
        await settled(pilot)
        await pilot.press("slash", *"lantern glow")
        field = app.query_one("#my-work #search", Input)
        left = field.content_region.x - field.region.x
        top = field.content_region.y - field.region.y
        await drag(pilot, field, (left, top), (left + len("lantern"), top))
        await pilot.press("ctrl+c")
        assert copied == ["lantern"]


async def test_clicking_a_saved_filter_chooses_it_and_clicking_it_again_runs_it():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await click_tab(pilot, "Filters")
        filters = app.query_one(Filters)
        sidebar = filters.query_one("#sidebar", OptionList)
        assert filters.running == filters.filters[0]

        await click_option(pilot, sidebar, 2)
        assert filters.running == filters.filters[0]
        await click_option(pilot, sidebar, 2)
        assert filters.running == filters.filters[2]


async def test_filter_form_and_delete_buttons_are_clickable():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        await click_tab(pilot, "Filters")
        filters = app.query_one(Filters)
        filters.query_one("#sidebar").focus()
        await pilot.press("n", *"Mine")
        await pilot.click("#query")
        await pilot.press(*"assignee:@me")
        await pilot.click("#save")
        await settled(pilot)
        assert filters.filters[-1] == SavedFilter("Mine", "assignee:@me")

        await pilot.press("x")
        await pilot.click("#yes")
        await settled(pilot)
        assert [f.name for f in filters.filters] == [f.name for f in demo.config().filters]


async def test_preferences_options_checkboxes_and_buttons_are_clickable():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(100, 80)) as pilot:
        await settled(pilot)
        await pilot.press("S")
        await until(pilot, lambda: isinstance(app.screen, PreferencesScreen))
        preferences = app.screen
        theme = sorted(app.available_themes)[0]
        await click_option(pilot, preferences.query_one("#theme", OptionList), 0)
        await until(pilot, lambda: app.theme == theme)  # previewed; the message can lag on CI
        await click_option(pilot, preferences.query_one(StatusList), 0)  # Todo, now active
        await pilot.click("#show-done")
        await pilot.click("Button.-primary")  # Save
        await settled(pilot)

    preferences = app.config.preferences
    assert (preferences.theme, preferences.show_done) == (theme, True)
    assert app.config.statuses[0].active


async def test_question_mark_lists_the_mouse_and_copy_and_scrolls_with_the_wheel():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test(size=(100, 20)) as pilot:
        await settled(pilot)
        await pilot.press("question_mark")
        await until(pilot, lambda: isinstance(app.screen, KeysScreen))
        text = "\n".join(str(widget.render()) for widget in app.screen.query(Static))
        lines = {" ".join(line.split()) for line in text.splitlines()}
        assert {"Mouse", "^c Copy", "wheel Scroll"} <= lines

        keys = app.screen.query_one("#keys")
        await wheel(pilot, keys)
        assert keys.scroll_y > 0


async def test_setup_checkboxes_and_buttons_are_clickable(tmp_path):
    app = SetupApp(demo.github(), tmp_path / "config.toml")
    async with app.run_test(size=(100, 50)) as pilot:
        await settled(pilot)
        repos = app.screen.query_one(SelectionList)
        assert repos.selected == ["octo-dev/lanternfish", "octo-dev/tidepool"]
        await click_option(pilot, repos, 0)
        assert repos.selected == ["octo-dev/tidepool"]

        await pilot.click("#next")
        await settled(pilot)
        assert isinstance(app.screen, SourcesScreen)


async def test_forms_take_clicks_and_copy_selected_text():
    copied: list[str] = []
    github = demo.github()
    app = LazyIssuesApp(demo.config(), github, system_clipboard=copied.append)
    async with app.run_test(size=(110, 40)) as pilot:
        await settled(pilot)
        await click_row(pilot, 1)  # lanternfish#9, assigned to octo-dev
        await pilot.press("a")
        await settled(pilot)
        form = app.screen

        label = form.query_one("Label")
        await drag(pilot, label, (0, 0), (8, 0))  # "Assignees"
        await until(pilot, lambda: copy_key(app) is not None)  # the footer redraws on a refresh
        await pilot.press("ctrl+c")
        assert copied == ["Assignees"]

        await click_option(pilot, form.query_one(SelectionList), 0)  # sam-reef, the team
        await pilot.click("#submit")
        await settled(pilot)
        assert not isinstance(app.screen, type(form))
        assignees = (await github.issue_detail("octo-dev/lanternfish", 9)).issue.assignees
        assert assignees == ("octo-dev", "sam-reef")
