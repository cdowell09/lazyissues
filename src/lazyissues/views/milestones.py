"""Milestones: each repo's open milestones with progress, their issues by status."""

import asyncio

from lazyissues.models import Milestone
from lazyissues.view_model import Group, Grouping, by_milestone, pinned, progress_bar
from lazyissues.views.issue_list import IssueList


class Milestones(IssueList):
    LABEL = "Milestones"
    EMPTY = "No open milestones in the repo set."
    found: list[Milestone] | None = None  # looked up on each refresh

    @property
    def milestones(self) -> list[Milestone]:
        """The milestones the tab shows, pinned ones only when config pins any. Before
        the first refresh answers, those of the snapshot's issues, without progress."""
        found = self.found
        if found is None:
            found = list(
                dict.fromkeys(
                    Milestone(issue.repo, issue.milestone)
                    for issue in self.store.issues
                    if issue.milestone
                )
            )
        return pinned(found, self.config.pinned_milestones)

    async def queries(self) -> list[str]:
        repos = self.config.repo_names
        found = await asyncio.gather(*(self.github.repo_milestones(repo) for repo in repos))
        self.found = [milestone for milestones in found for milestone in milestones]
        return [f'is:issue repo:{m.repo} milestone:"{m.title}"' for m in self.milestones]

    def grouping(self) -> Grouping:
        shown = [m for m in self.milestones if self.state.repo in (None, m.repo)]
        return by_milestone(self.rules, shown)

    def header(self, group: Group) -> str:
        if self.found is None:
            return super().header(group)
        milestone = next(m for m in self.milestones if m.name == group.name)
        done = milestone.closed
        return f"{super().header(group)}  {progress_bar(done, milestone.open + done)}"
