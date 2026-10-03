"""What a list tab shows: issues + status rules + view state -> visible groups.

Pure, with no Textual, so every tab's grouping and filtering is unit-tested here and
the list widget only draws what `visible_groups` returns.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from lazyissues.models import Issue, Milestone
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
    # Group names, and the keys of parent issues whose sub-issues are hidden.
    folded: frozenset[str] = frozenset()
    selected: frozenset[str] = frozenset()  # issue keys, for a bulk action

    @property
    def filtering(self) -> bool:
        """Whether a search, status focus or repo filter hides some issues."""
        return (self.search, self.focus, self.repo) != ("", None, None)

    def toggle_fold(self, name: str) -> "ViewState":
        """Fold or unfold a group by name, or a parent's sub-issues by its key."""
        return replace(self, folded=self.folded ^ {name})

    def toggle_selected(self, keys: Iterable[str]) -> "ViewState":
        """Select the issues `keys`, or unselect them when they are all selected."""
        keys = frozenset(keys)
        if keys <= self.selected:
            return replace(self, selected=self.selected - keys)
        return replace(self, selected=self.selected | keys)

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
class Row:
    """An issue as a group lists it: its sub-issues in the group follow it, indented."""

    issue: Issue
    # One entry per parent above it in the group, outermost first: whether a later
    # sub-issue of that parent follows, so the tree's line carries on down past this row.
    continues: tuple[bool, ...] = ()
    has_sub_issues: bool = False  # in the group, shown or folded
    folded: bool = False  # its sub-issues are hidden

    @property
    def depth(self) -> int:
        """How many parents above it in the group."""
        return len(self.continues)

    @property
    def lead(self) -> str | None:
        """The parent's ref, which the row leads with when the parent isn't above it."""
        return None if self.depth else self.issue.parent_ref

    @property
    def fold_key(self) -> str | None:
        """What `z` on this row folds: the issue's own sub-issues, else those it is one
        of; None means the group."""
        if self.has_sub_issues:
            return self.issue.key
        return self.issue.parent if self.depth else None


@dataclass(frozen=True)
class Group:
    name: str
    issues: list[Issue]  # matching issues, whether folded or not
    rows: list[Row]  # empty when folded
    folded: bool = False

    @property
    def total(self) -> int:
        return len(self.issues)


def by_status(rules: StatusRules) -> Grouping:
    return lambda issues: [(g.name, g.issues) for g in rules.group(issues)]


def by_assignee(rules: StatusRules, members: list[str]) -> Grouping:
    """A group per member, even with nothing assigned, ordered by how many active
    issues each has; within a member, active issues first, then status order."""

    def group(issues: list[Issue]) -> list[tuple[str, list[Issue]]]:
        ordered = sorted(_in_status_order(rules, issues), key=lambda i: not _active(rules, i))
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


def by_milestone(rules: StatusRules, milestones: list[Milestone]) -> Grouping:
    """A group per milestone, in the order given, even with no issues; issues in status
    order."""

    def group(issues: list[Issue]) -> list[tuple[str, list[Issue]]]:
        ordered = _in_status_order(rules, issues)
        return [
            (m.name, [i for i in ordered if (i.repo, i.milestone) == (m.repo, m.title)])
            for m in milestones
        ]

    return group


def pinned(milestones: list[Milestone], keys: list[str]) -> list[Milestone]:
    """The milestones config's `pinned_milestones` names, in its order; all when it's
    empty. Keys match ignoring case, as GitHub's names do."""
    if not keys:
        return milestones
    by_key = {m.key.casefold(): m for m in milestones}
    return [by_key[key.casefold()] for key in keys if key.casefold() in by_key]


def progress_bar(done: int, total: int) -> str:
    filled = round(10 * done / total) if total else 0
    return f"{'█' * filled}{'░' * (10 - filled)} {done}/{total}"


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
        rows = [] if folded else _nested(members, state.folded)
        groups.append(Group(name, members, rows, folded))
    return groups


def _nested(issues: list[Issue], folded: frozenset[str]) -> list[Row]:
    """`issues` in order, with each one's sub-issues in the list moved under it; those
    of a parent in `folded` are left out."""
    keys = {issue.key for issue in issues}
    sub_issues: dict[str, list[Issue]] = {}
    for issue in issues:
        if issue.parent in keys:
            sub_issues.setdefault(issue.parent, []).append(issue)
    rows: list[Row] = []

    def add(issue: Issue, continues: tuple[bool, ...]) -> None:
        subs = sub_issues.get(issue.key, [])
        hide = bool(subs) and issue.key in folded
        rows.append(Row(issue, continues, bool(subs), hide))
        shown = [] if hide else subs
        for at, sub in enumerate(shown):
            add(sub, (*continues, at < len(shown) - 1))

    for issue in issues:
        if issue.parent not in keys:
            add(issue, ())
    return rows


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


def _in_status_order(rules: StatusRules, issues: list[Issue]) -> list[Issue]:
    return [issue for group in rules.group(issues) for issue in group.issues]


def _active(rules: StatusRules, issue: Issue) -> bool:
    return rules.is_active(rules.status_of(issue).name)
