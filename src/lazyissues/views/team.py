"""Team: open issues grouped by assignee, for the team roster and the current user."""

from collections.abc import Sequence

from lazyissues.models import Issue
from lazyissues.view_model import Grouping, by_assignee
from lazyissues.views.issue_list import IssueList


class Team(IssueList):
    LABEL = "Team"
    EMPTY = "Add GitHub logins to `team` in config.toml."
    USE_REPO_SYNC = True
    viewer: str | None = None  # set from the first page response, for the grouping

    @property
    def members(self) -> list[str]:
        roster = self.config.team
        if self.viewer is None or self.viewer.casefold() in {m.casefold() for m in roster}:
            return list(roster)
        return [*roster, self.viewer]

    def _needs_viewer(self) -> bool:
        return True

    def record_viewer(self, viewer: str | None) -> None:
        if viewer:
            self.viewer = viewer

    def derive_open(self, issues: Sequence[Issue], viewer: str | None) -> list[Issue]:
        if viewer:
            self.viewer = viewer
        members_cf = {member.casefold() for member in self.members}
        return [
            issue
            for issue in issues
            if any(assignee.casefold() in members_cf for assignee in issue.assignees)
        ]

    async def queries(self) -> list[str]:
        if self.viewer is None:
            return [
                *(f"is:issue assignee:{member}" for member in self.config.team),
                "is:issue assignee:@me",
            ]
        return [f"is:issue assignee:{member}" for member in self.members]

    def grouping(self) -> Grouping:
        return by_assignee(self.rules, self.members)
