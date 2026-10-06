"""`e`: edit an issue's title, body, labels and milestone."""

import asyncio

from textual.app import ComposeResult

from lazyissues.forms.form import Form, IssueFields
from lazyissues.github import Gateway
from lazyissues.models import Issue, IssueChanges, IssueDetail
from lazyissues.statuses import StatusRules


class EditForm(Form):
    """Starts from the cached detail (GitHub's copy when there is none) and sends only what
    changed: the changed fields, and the labels added and removed. So an edit made elsewhere
    meanwhile is kept, and status labels, which only a move changes, are never offered or
    touched."""

    def __init__(
        self,
        github: Gateway,
        issue: Issue,
        rules: StatusRules,
        details: dict[str, IssueDetail],
    ) -> None:
        super().__init__(github, f"Edit {issue.ref}")
        self.issue = issue
        self.details = details  # the app's detail cache
        self.rules = rules
        self.before: IssueDetail | None = None  # GitHub's copy when the form opened

    def fields(self) -> ComposeResult:
        yield IssueFields()

    async def load(self) -> None:
        repo = self.issue.repo
        before, labels, milestones = await asyncio.gather(
            self._detail(),
            self.github.repo_labels(repo),
            self.github.repo_milestones(repo),
        )
        fields = self.query_one(IssueFields)
        fields.fill(before.issue.title, before.body)
        fields.offer(
            self._ordinary(labels),
            [milestone.title for milestone in milestones],
            picked=self._ordinary(before.issue.labels),
            milestone=before.issue.milestone,
        )
        self.before = before

    async def _detail(self) -> IssueDetail:
        return self.details.get(self.issue.key) or await self.github.issue_detail(
            self.issue.repo, self.issue.number
        )

    def _ordinary(self, labels: tuple[str, ...] | list[str]) -> list[str]:
        return self.rules.ordinary_labels(self.rules.repo_of(self.issue), labels)

    async def save(self) -> IssueDetail | None:
        before = self.before
        assert before is not None  # the form saves only once loaded
        draft = self.query_one(IssueFields).draft()
        repo, number = self.issue.repo, self.issue.number
        had = self._ordinary(before.issue.labels)
        add = [label for label in draft.labels if label not in had]
        remove = [label for label in had if label not in draft.labels]
        changes: IssueChanges = {}
        if draft.title != before.issue.title:
            changes["title"] = draft.title
        if draft.body != before.body:
            changes["body"] = draft.body
        if draft.milestone != before.issue.milestone:
            changes["milestone"] = draft.milestone
        if not (add or remove or changes):
            return None
        if add:
            await self.github.add_labels(repo, number, add)
        if remove:
            await self.github.remove_labels(repo, number, remove)
        if changes:
            return await self.github.update_issue(repo, number, changes)
        return await self.github.issue_detail(repo, number)
