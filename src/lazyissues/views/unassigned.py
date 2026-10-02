"""Unassigned: open issues with no assignee across the repo set."""

from lazyissues.views.issue_list import IssueList


class Unassigned(IssueList):
    LABEL = "Unassigned"
    QUERY = "is:issue no:assignee"
    EMPTY = "Every open issue has an assignee."
