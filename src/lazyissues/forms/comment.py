"""`C`: comment on an issue."""

from textual.app import ComposeResult

from lazyissues.forms.form import Form, FormError, TextField
from lazyissues.github import Gateway
from lazyissues.models import Issue, IssueDetail


class CommentForm(Form):
    REGROUPS = False  # no tab lists issues by their comments

    def __init__(self, github: Gateway, issue: Issue) -> None:
        super().__init__(github, f"Comment on {issue.ref}  {issue.title}")
        self.issue = issue

    def fields(self) -> ComposeResult:
        yield TextField(id="comment", compact=True)

    async def save(self) -> IssueDetail:
        body = self.query_one(TextField).text
        if not body.strip():
            raise FormError("Write a comment first.")
        return await self.github.comment(self.issue.repo, self.issue.number, body)
