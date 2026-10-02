"""Team: open issues grouped by assignee, for the team roster and the current user."""

from lazyissues.view_model import Grouping, by_assignee
from lazyissues.views.issue_list import IssueList


class Team(IssueList):
    LABEL = "Team"
    EMPTY = "Add GitHub logins to `team` in config.toml."
    viewer: str | None = None  # looked up on the first refresh

    @property
    def members(self) -> list[str]:
        roster = self.config.team
        if self.viewer is None or self.viewer.casefold() in {m.casefold() for m in roster}:
            return list(roster)
        return [*roster, self.viewer]

    async def queries(self) -> list[str]:
        if self.viewer is None:
            self.viewer, _ = await self.github.whoami()
        return [f"is:issue assignee:{member}" for member in self.members]

    def grouping(self) -> Grouping:
        return by_assignee(self.rules, self.members)
