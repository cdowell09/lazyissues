"""My Work: open issues assigned to the current user across the repo set."""

from lazyissues.views.issue_list import IssueList


class MyWork(IssueList):
    LABEL = "My Work"
    QUERY = "is:issue assignee:@me"
    EMPTY = "No open issues are assigned to you."
