"""`c`: create an issue, optionally with a status."""

import asyncio
from dataclasses import replace

from textual.app import ComposeResult
from textual.widgets import Select

from lazyissues.config import Config, Repo
from lazyissues.forms.form import Form, IssueFields, Written, choices, label
from lazyissues.github import Gateway, GitHubError
from lazyissues.models import IssueDetail
from lazyissues.statuses import StatusRules


class CreateForm(Form):
    """Creates the issue. Its chosen `status` is left to the caller to set as a move,
    which adds the status label or puts the issue on the project, as any move does."""

    def __init__(self, github: Gateway, config: Config, repo: str) -> None:
        super().__init__(github, "New issue")
        self.repos = {r.name: r for r in config.repos}
        self.rules = StatusRules(config)
        self.default_repo = repo

    def fields(self) -> ComposeResult:
        yield label("Repository")
        yield Select(
            choices(self.repos), value=self.default_repo, allow_blank=False, id="repo", compact=True
        )
        yield IssueFields()
        yield label("Status")
        yield Select[str]([], prompt="No status", id="status", compact=True)

    @property
    def repo(self) -> Repo:
        name = self.query_one("#repo", Select).selection  # never blank: no blank allowed
        return self.repos[name or self.default_repo]

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "repo":
            self.reload()

    async def load(self) -> None:
        repo = self.repo
        labels, milestones, options = await asyncio.gather(
            self.github.repo_labels(repo.name),
            self.github.repo_milestones(repo.name),
            self._project_options(repo),
        )
        fields = self.query_one(IssueFields)
        # A status label is set by the Status choice, as a move.
        fields.offer(self.rules.ordinary_labels(repo, labels), [m.title for m in milestones])
        status = self.query_one("#status", Select)
        status.set_options(choices(self.rules.offered(repo, options)))

    async def _project_options(self, repo: Repo) -> list[str]:
        """The project's Status options; none, with a warning, if GitHub won't say (a
        token without the `project` scope), so the issue can still be created."""
        if repo.project is None:
            return []
        try:
            return await self.github.project_status_options(repo.project)
        except GitHubError as e:
            self.notify(str(e), title="No statuses to choose from", severity="warning")
            return []

    def written(self, detail: IssueDetail, sent_at: float) -> Written:
        status = self.query_one("#status", Select).selection
        return replace(super().written(detail, sent_at), status=status)

    async def save(self) -> IssueDetail:
        draft = self.query_one(IssueFields).draft()
        return await self.github.create_issue(
            self.repo.name,
            draft.title,
            body=draft.body,
            labels=draft.labels,
            milestone=draft.milestone,
        )
