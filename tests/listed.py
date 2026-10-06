"""Reading a list tab's rows, as drawn or as plain text.

Most tests are about which groups and issues a tab lists, not how they're drawn, so they
read rows `plain`: `In Progress (2)`, `lanternfish#9 → lanternfish#11`. Tests of the
drawing itself (indentation, fold arrows, tree lines) read them `drawn`.
"""

import re

from textual.pilot import Pilot
from textual.widgets import DataTable, TabbedContent

from lazyissues.views.issue_list import FOLDED, UNFOLDED

# A row's drawing: its indentation, fold arrow and sub-issue tree lines.
DRAWING = re.compile(f"^[ {FOLDED}{UNFOLDED}│├└]+")


def drawn(table: DataTable) -> list[str]:
    """Each row's Issue cell as drawn: group headers and issue refs."""
    return [str(table.get_row_at(i)[1]) for i in range(table.row_count)]


def plain(table: DataTable) -> list[str]:
    """Each row's Issue cell without its drawing."""
    return [DRAWING.sub("", text) for text in drawn(table)]


async def show_tab(pilot: Pilot, view: str) -> None:
    """Open the tab holding `view` and wait for its first refresh: tabs refresh when shown."""
    await pilot.pause()  # the start tab has to be drawn before another is shown
    pane = pilot.app.query_one(f"#{view}").parent
    assert pane is not None and pane.id is not None
    pilot.app.query_one(TabbedContent).active = pane.id
    await pilot.pause()
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()
