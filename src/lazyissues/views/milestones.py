"""Milestones: each repo's open milestones with progress, their issues by status."""

import asyncio
from collections.abc import Sequence

from rich.text import Text

from lazyissues.models import Issue, Milestone
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

    async def prepare_sync(self, *, force: bool) -> None:
        repos = self.config.repo_names
        fresh = force or self.found is None
        found = await asyncio.gather(
            *(self.github.repo_milestones(repo, fresh=fresh) for repo in repos)
        )
        self.found = [milestone for milestones in found for milestone in milestones]

    def derive_open(self, issues: Sequence[Issue], viewer: str | None) -> list[Issue]:
        allowed = {(m.repo.casefold(), m.title) for m in self.milestones}
        return [
            issue
            for issue in issues
            if issue.milestone and (issue.repo.casefold(), issue.milestone) in allowed
        ]

    async def queries(self) -> list[str]:
        if self.repo_sync is None or self.found is None:
            await self.prepare_sync(force=True)
        return [f'is:issue repo:{m.repo} milestone:"{m.title}"' for m in self.milestones]

    def grouping(self) -> Grouping:
        shown = [m for m in self.milestones if self.state.repo in (None, m.repo)]
        return by_milestone(shown)

    def header(self, group: Group) -> Text:
        header = super().header(group)
        milestone = next((m for m in self.found or [] if m.name == group.name), None)
        if milestone is None:  # a snapshot's milestone, until GitHub gives its progress
            return header
        done = milestone.closed
        return header.append(f"  {progress_bar(done, milestone.open + done)}")
