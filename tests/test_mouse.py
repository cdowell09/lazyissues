"""The mouse: clicking, wheel scrolling, and selecting text to copy."""

from collections.abc import Callable

from textual import events
from textual.app import App
from textual.pilot import Pilot
from textual.widget import Widget
from textual.widgets import DataTable, Input, OptionList, SelectionList, Tab, TabbedContent

from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.detail import IssueDetailScreen
from lazyissues.models import CloseReason
from lazyissues.move_picker import MovePicker
from lazyissues.move_planner import Close
from lazyissues.setup import SetupApp, SourcesScreen


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
    return str(table(app, view).get_row_at(row)[0])


async def click_row(pilot: Pilot, row: int, x: int = 2, view: str = "my-work") -> None:
    """Click `row` of `view`'s list (0 is the first row under the column headers)."""
    await pilot.click(table(pilot.app, view), offset=(x, row + 1))
    await pilot.pause()


async def click_option(pilot: Pilot, options: OptionList, index: int) -> None:
    """Click the `index`th option of a list of one-line options."""
    top = options.content_region.y - options.region.y
    await pilot.click(options, offset=(2, top + index))
    await pilot.pause()


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
        assert app.screen.issue.ref == first_cell(app, 1)


async def test_clicking_a_group_header_folds_and_unfolds_it():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        header = first_cell(app, 0)
        await click_row(pilot, 1)  # an issue, so the header isn't selected
        await click_row(pilot, 0)
        assert first_cell(app, 0) == f"▸ {header}"
        await click_row(pilot, 0)
        assert first_cell(app, 0) == header


async def test_clicking_a_tab_shows_it_and_its_rows_are_clickable():
    app = LazyIssuesApp(demo.config(), demo.github())
    async with app.run_test() as pilot:
        await settled(pilot)
        team = next(tab for tab in app.query(Tab) if str(tab.label) == "Team")
        await pilot.click(team)
        assert app.query_one(TabbedContent).active_pane is app.query_one("#team").parent

        await click_row(pilot, 1, view="team")
        await click_row(pilot, 1, view="team")
        assert isinstance(app.screen, IssueDetailScreen)
        assert app.screen.issue.ref == first_cell(app, 1, "team")


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
        assert table(app).scroll_y == 0
        await wheel(pilot, table(app))
        assert table(app).scroll_y > 0

        detail = (await open_detail(pilot)).query_one("#detail")
        await wheel(pilot, detail)
        assert detail.scroll_y > 0


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
    async with app.run_test() as pilot:
        await settled(pilot)
        await click_row(pilot, 1)
        ref = first_cell(app, 1)
        # A cell's text starts one column in, after its padding; row 1 is under the headers.
        await drag(pilot, table(app), (1, 2), (len(ref), 2))
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
        key = copy_key(app)
        assert key is not None
        await pilot.click(key)
        assert copied == [ref]


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
