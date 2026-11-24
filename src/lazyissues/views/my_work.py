"""My Work: open issues assigned to the current user across the repo set."""

from collections.abc import Sequence

from lazyissues.models import Issue
from lazyissues.views.issue_list import IssueList


class MyWork(IssueList):
    LABEL = "My Work"
    QUERY = "is:issue assignee:@me"
    EMPTY = "No open issues are assigned to you."
    NEEDS_VIEWER = True

    def derive_open(self, issues: Sequence[Issue], viewer: str | None) -> list[Issue]:
        if not viewer:
            return []
        viewer_cf = viewer.casefold()
        return [
            issue
            for issue in issues
            if any(assignee.casefold() == viewer_cf for assignee in issue.assignees)
        ]
