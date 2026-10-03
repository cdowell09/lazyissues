"""Reading a list tab's rows, as drawn or as plain text.

Most tests are about which groups and issues a tab lists, not how they're drawn, so they
read rows `plain`: `In Progress (2)`, `lanternfish#9 → lanternfish#11`. Tests of the
drawing itself (indentation, fold arrows, tree lines) read them `drawn`.
"""

import re

from textual.widgets import DataTable

from lazyissues.views.issue_list import FOLDED, UNFOLDED

# A row's drawing: its indentation, fold arrow and sub-issue tree lines.
DRAWING = re.compile(f"^[ {FOLDED}{UNFOLDED}│├└]+")


def drawn(table: DataTable) -> list[str]:
    """Each row's Issue cell as drawn: group headers and issue refs."""
    return [str(table.get_row_at(i)[1]) for i in range(table.row_count)]


def plain(table: DataTable) -> list[str]:
    """Each row's Issue cell without its drawing."""
    return [DRAWING.sub("", text) for text in drawn(table)]
