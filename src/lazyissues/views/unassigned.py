"""Unassigned: open issues with no assignee across the repo set."""

from collections.abc import Sequence

from lazyissues.models import Issue
from lazyissues.views.issue_list import IssueList


class Unassigned(IssueList):
    LABEL = "Unassigned"
    QUERY = "is:issue no:assignee"
    EMPTY = "Every open issue has an assignee."
    USE_REPO_SYNC = True

    def derive_open(self, issues: Sequence[Issue], viewer: str | None) -> list[Issue]:
        return [issue for issue in issues if not issue.assignees]
