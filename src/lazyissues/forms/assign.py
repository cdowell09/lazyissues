"""`a`: assign or reassign an issue."""

import asyncio
from collections.abc import Sequence

from textual.app import ComposeResult

from lazyissues.forms.form import Form, Picker, label
from lazyissues.github import Gateway
from lazyissues.models import Issue, IssueDetail


def team_first(roster: Sequence[str], users: Sequence[str]) -> list[str]:
    """The assignable `users`, those on the team `roster` first, each once and spelled as
    GitHub spells it: logins ignore case."""
    assignable = {user.casefold(): user for user in users}
    team = [assignable[login.casefold()] for login in roster if login.casefold() in assignable]
    return list(dict.fromkeys([*team, *users]))


class AssignForm(Form):
    """Starts from GitHub's latest assignees and sends only who was added and who was
    removed, so an assignee someone else changed meanwhile stays as they left it."""

    def __init__(self, github: Gateway, issue: Issue, roster: Sequence[str]) -> None:
        super().__init__(github, f"Assign {issue.ref}  {issue.title}")
        self.issue = issue
        self.roster = roster
        self.assignees: tuple[str, ...] = issue.assignees  # GitHub's, once loaded

    def fields(self) -> ComposeResult:
        yield label("Assignees (Space picks, Enter assigns)")
        yield Picker(id="assignees")

    async def load(self) -> None:
        users, detail = await asyncio.gather(
            self.github.assignable_users(self.issue.repo),
            self.github.issue_detail(self.issue.repo, self.issue.number),
        )
        self.assignees = detail.issue.assignees
        picker = self.query_one(Picker)
        picker.set_items([*team_first(self.roster, users), *self.assignees], self.assignees)

    async def save(self) -> IssueDetail | None:
        picked = self.query_one(Picker).selected
        add = [login for login in picked if login not in self.assignees]
        remove = [login for login in self.assignees if login not in picked]
        if not add and not remove:
            return None
        return await self.github.change_assignees(
            self.issue.repo, self.issue.number, add=add, remove=remove
        )
