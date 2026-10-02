"""What a list tab shows: issues + status rules + view state -> visible groups.

Pure, with no Textual, so every tab's grouping and filtering is unit-tested here and
the list widget only draws what `visible_groups` returns.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from lazyissues.models import Issue
from lazyissues.statuses import StatusRules

# Splits issues into named groups in display order; a tab's grouping.
Grouping = Callable[[list[Issue]], list[tuple[str, list[Issue]]]]


@dataclass(frozen=True)
class ViewState:
    """One tab's filters and folds, kept per tab for the session."""

    search: str = ""
    focus: str | None = None  # a status name; only issues with it show
    show_done: bool = False
    repo: str | None = None  # only this repo's issues show
    folded: frozenset[str] = frozenset()  # group names

    @property
    def filtering(self) -> bool:
        """Whether a search, status focus or repo filter hides some issues."""
        return (self.search, self.focus, self.repo) != ("", None, None)

    def toggle_fold(self, group: str) -> "ViewState":
        return replace(self, folded=self.folded ^ {group})

    def fold_all(self, groups: list[str]) -> "ViewState":
        """Fold every group, or unfold them all when they are all folded."""
        if self.folded.issuperset(groups):
            return replace(self, folded=self.folded - set(groups))
        return replace(self, folded=self.folded | set(groups))

    def cycle_focus(self, statuses: list[str], step: int) -> "ViewState":
        return replace(self, focus=_cycle(statuses, self.focus, step))

    def cycle_repo(self, repos: list[str]) -> "ViewState":
        return replace(self, repo=_cycle(repos, self.repo, 1))


@dataclass(frozen=True)
class Group:
    name: str
    total: int  # matching issues, whether folded or not
    rows: list[Issue]  # empty when folded
    folded: bool = False


def by_status(rules: StatusRules) -> Grouping:
    return lambda issues: [(g.name, g.issues) for g in rules.group(issues)]


def by_assignee(rules: StatusRules, members: list[str]) -> Grouping:
    """A group per member, even with nothing assigned, ordered by how many active
    issues each has; within a member, active issues first, then status order."""

    def group(issues: list[Issue]) -> list[tuple[str, list[Issue]]]:
        in_order = [issue for g in rules.group(issues) for issue in g.issues]
        ordered = sorted(in_order, key=lambda issue: not _active(rules, issue))
        groups = [
            # GitHub logins ignore case, and the roster is typed by hand.
            (
                member,
                [i for i in ordered if member.casefold() in {a.casefold() for a in i.assignees}],
            )
            for member in members
        ]
        return sorted(groups, key=lambda g: -sum(_active(rules, i) for i in g[1]))

    return group


def visible_groups(
    issues: list[Issue], grouping: Grouping, rules: StatusRules, state: ViewState
) -> list[Group]:
    matching = [
        issue
        for issue in _filtered(issues, rules, state)
        if state.focus in (None, rules.status_of(issue).name)
    ]
    groups = []
    for name, members in grouping(matching):
        if not members and not _contains(name, state.search):
            continue  # an empty member group shows unless a search names someone else
        folded = name in state.folded
        groups.append(Group(name, len(members), [] if folded else members, folded))
    return groups


def focusable_statuses(issues: list[Issue], rules: StatusRules, state: ViewState) -> list[str]:
    """The statuses status focus steps through: those of the issues the other filters
    leave, in display order."""
    return [group.name for group in rules.group(_filtered(issues, rules, state))]


def _filtered(issues: list[Issue], rules: StatusRules, state: ViewState) -> list[Issue]:
    """Issues passing every filter except status focus."""
    return [
        issue
        for issue in issues
        if (state.show_done or not rules.is_done(issue))
        and state.repo in (None, issue.repo)
        and _matches(issue, state.search)
    ]


def _cycle(options: list[str], current: str | None, step: int) -> str | None:
    """Step through `options` and then back to None (no filter)."""
    choices = [None, *options]
    at = choices.index(current) if current in choices else 0
    return choices[(at + step) % len(choices)]


def _matches(issue: Issue, text: str) -> bool:
    """Search: number, title, assignees and labels, ignoring case."""
    fields = (f"#{issue.number}", issue.title, *issue.assignees, *issue.labels)
    return any(_contains(field, text) for field in fields)


def _contains(field: str, text: str) -> bool:
    return text.casefold() in field.casefold()


def _active(rules: StatusRules, issue: Issue) -> bool:
    return rules.is_active(rules.status_of(issue).name)
