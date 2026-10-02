"""An in-memory GitHub, used by the test suite and by `--demo`."""

import shlex
from dataclasses import dataclass, field, replace

from lazyissues.models import Issue


@dataclass
class FakeGitHub:
    viewer: str
    issues: list[Issue] = field(default_factory=list)
    closed: set[str] = field(default_factory=set)  # issue keys

    async def search_issues(self, query: str) -> list[Issue]:
        return [
            replace(issue, closed=issue.key in self.closed)
            for issue in self.issues
            if self._matches(issue, query)
        ]

    def _matches(self, issue: Issue, query: str) -> bool:
        """Understands the subset of GitHub search syntax the app sends."""
        repos: list[str] = []
        for term in shlex.split(query):
            qualifier, _, value = term.partition(":")
            match qualifier:
                case "is" if value == "issue":
                    pass
                case "is" | "state":
                    if (issue.key in self.closed) != (value == "closed"):
                        return False
                case "repo":
                    repos.append(value)
                case "assignee":
                    if (self.viewer if value == "@me" else value) not in issue.assignees:
                        return False
                case "no" if value == "assignee":
                    if issue.assignees:
                        return False
                case "label":
                    if value not in issue.labels:
                        return False
                case _:
                    if term.lower() not in issue.title.lower():
                        return False
        return not repos or issue.repo in repos
