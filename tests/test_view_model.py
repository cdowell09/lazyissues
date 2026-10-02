from lazyissues.config import Config, Repo, Status
from lazyissues.models import Issue, Milestone
from lazyissues.statuses import DONE, NO_STATUS, StatusRules
from lazyissues.view_model import (
    ViewState,
    by_assignee,
    by_milestone,
    by_status,
    focusable_statuses,
    pinned,
    progress_bar,
    visible_groups,
)

CONFIG = Config(
    repos=[Repo("a/x"), Repo("a/y")],
    statuses=[Status("Todo"), Status("In Progress", active=True)],
)
RULES = StatusRules(CONFIG)


def issue(number: int, *labels: str, repo: str = "a/x", title: str = "", **fields) -> Issue:
    return Issue(
        repo, number, title or f"Issue {number}", f"https://github.com/{repo}/issues/{number}",
        labels=labels, **fields,
    )  # fmt: skip


ISSUES = [
    issue(1, "todo", title="Fix the login form", assignees=("ana",)),
    issue(2, "in-progress", "bug", repo="a/y"),
    issue(3, "todo", repo="a/y"),
    issue(4),
    issue(5, "in-progress", closed=True),
]


DEFAULT = ViewState()
SHOW_DONE = ViewState(show_done=True)


def shown(state: ViewState = DEFAULT) -> list:
    """Each visible group as (name, total, numbers of the rows it shows)."""
    return [
        (g.name, g.total, [r.issue.number for r in g.rows])
        for g in visible_groups(ISSUES, by_status(RULES), RULES, state)
    ]


def test_groups_open_issues_by_status_with_totals_and_hides_done():
    assert shown() == [
        (NO_STATUS, 1, [4]),
        ("Todo", 2, [1, 3]),
        ("In Progress", 1, [2]),
    ]


def test_showing_done_adds_a_done_group_last():
    assert shown(ViewState(show_done=True))[-1] == (DONE, 1, [5])


def test_search_matches_number_title_assignee_and_labels_ignoring_case():
    def found(text: str) -> list[int]:
        return [n for _, _, rows in shown(ViewState(search=text)) for n in rows]

    assert found("#3") == [3]
    assert found("LOGIN") == [1]
    assert found("an") == [1]  # assignee ana
    assert found("bug") == [2]
    assert found("nothing like it") == []


def test_repo_filter_keeps_one_repo():
    assert shown(ViewState(repo="a/y")) == [("Todo", 1, [3]), ("In Progress", 1, [2])]


def test_status_focus_keeps_one_status():
    assert shown(ViewState(focus="In Progress")) == [("In Progress", 1, [2])]
    assert shown(ViewState(focus=DONE, show_done=True)) == [(DONE, 1, [5])]


def test_a_folded_group_keeps_its_header_and_total_but_hides_its_rows():
    state = ViewState().toggle_fold("Todo")
    assert shown(state)[1] == ("Todo", 2, [])
    assert [g.folded for g in visible_groups(ISSUES, by_status(RULES), RULES, state)] == [
        False,
        True,
        False,
    ]
    assert shown(state.toggle_fold("Todo"))[1] == ("Todo", 2, [1, 3])


def test_fold_all_folds_every_group_then_unfolds_them_all():
    names = [NO_STATUS, "Todo", "In Progress"]
    folded = ViewState().toggle_fold("Todo").fold_all(names)
    assert [rows for _, _, rows in shown(folded)] == [[], [], []]
    assert [rows for _, _, rows in shown(folded.fold_all(names))] == [[4], [1, 3], [2]]


def test_focus_cycles_through_the_statuses_shown_then_back_to_all():
    options = focusable_statuses(ISSUES, RULES, ViewState(focus="Todo", repo="a/y"))
    assert options == ["Todo", "In Progress"]  # focus itself doesn't narrow the choice
    assert focusable_statuses(ISSUES, RULES, ViewState(show_done=True))[-1] == DONE

    state = ViewState()
    seen = []
    for _ in range(3):
        state = state.cycle_focus(options, 1)
        seen.append(state.focus)
    assert seen == ["Todo", "In Progress", None]
    assert ViewState().cycle_focus(options, -1).focus == "In Progress"


def test_search_focus_and_repo_filter_count_as_filtering_but_done_and_folds_do_not():
    assert not ViewState(show_done=True).toggle_fold("Todo").filtering
    assert ViewState(search="x").filtering
    assert ViewState(focus="Todo").filtering
    assert ViewState(repo="a/x").filtering


def test_repo_filter_cycles_through_the_repo_set_then_back_to_all():
    state = ViewState().cycle_repo(["a/x", "a/y"]).cycle_repo(["a/x", "a/y"])
    assert state.repo == "a/y"
    assert state.cycle_repo(["a/x", "a/y"]).repo is None


TEAM_ISSUES = [
    issue(1, "todo", assignees=("ana",)),
    issue(2, "todo", assignees=("Bo",)),
    issue(3, "in-progress", closed=True, assignees=("bo",)),
    issue(4, "in-progress", assignees=("bo", "ana")),
    issue(5, "in-progress", assignees=("bo",)),
    issue(6, "todo", assignees=("someone-else",)),
]


def team(state: ViewState = SHOW_DONE) -> list:
    grouping = by_assignee(RULES, ["ana", "cy", "bo"])
    return [
        (g.name, [r.issue.number for r in g.rows])
        for g in visible_groups(TEAM_ISSUES, grouping, RULES, state)
    ]


def test_team_groups_by_member_ordered_by_active_issues_with_active_before_done():
    assert team() == [
        ("bo", [4, 5, 2, 3]),  # two active, then the rest in status order, done last
        ("ana", [4, 1]),  # one active; a shared issue shows under each assignee
        ("cy", []),  # everyone on the roster shows, even with nothing assigned
    ]


def test_team_search_also_matches_members():
    assert team(ViewState(search="cy")) == [("cy", [])]
    assert team(ViewState(search="#1")) == [("ana", [1])]


def nested(issues: list[Issue], state: ViewState = DEFAULT) -> list:
    """Each visible group as (name, total, rows as (number, depth, lead))."""
    return [
        (g.name, g.total, [(r.issue.number, r.depth, r.lead) for r in g.rows])
        for g in visible_groups(issues, by_status(RULES), RULES, state)
    ]


FAMILY = [
    issue(1, "todo"),
    issue(2, "todo", parent="a/x#1"),
    issue(3, "todo"),
    issue(4, "todo", parent="a/x#2"),  # a sub-issue of a sub-issue
    issue(5, "in-progress", parent="a/x#1"),  # its parent is in another group
    issue(6, "todo", parent="a/y#9", repo="a/y"),  # its parent isn't loaded
]


def test_sub_issues_indent_under_a_parent_in_the_same_group():
    assert nested(FAMILY) == [
        ("Todo", 5, [(1, 0, None), (2, 1, None), (4, 2, None), (3, 0, None), (6, 0, "y#9")]),
        ("In Progress", 1, [(5, 0, "x#1")]),
    ]


def test_folding_a_parent_hides_its_sub_issues_but_the_total_counts_them():
    assert nested(FAMILY, ViewState().toggle_fold("a/x#1"))[0] == (
        "Todo",
        5,
        [(1, 0, None), (3, 0, None), (6, 0, "y#9")],
    )
    [todo, _] = visible_groups(FAMILY, by_status(RULES), RULES, ViewState().toggle_fold("a/x#1"))
    assert todo.rows[0].folded


def test_z_on_a_parent_or_its_sub_issue_folds_the_parent_and_otherwise_the_group():
    [todo, in_progress] = visible_groups(FAMILY, by_status(RULES), RULES, DEFAULT)
    # #2 is both: it folds its own sub-issue.
    assert [row.fold_key for row in todo.rows] == ["a/x#1", "a/x#2", "a/x#2", None, None]
    assert [row.fold_key for row in in_progress.rows] == [None]


MILESTONES = [Milestone("a/x", "v1"), Milestone("a/y", "v1"), Milestone("a/x", "Empty")]


def test_milestones_group_by_repo_and_title_with_issues_in_status_order():
    issues = [
        issue(1, "in-progress", milestone="v1"),
        issue(2, "todo", milestone="v1"),
        issue(3, "todo", repo="a/y", milestone="v1"),  # same title, another repo
        issue(4, "todo"),  # in no milestone
    ]
    groups = visible_groups(issues, by_milestone(RULES, MILESTONES), RULES, DEFAULT)
    assert [(g.name, [r.issue.number for r in g.rows]) for g in groups] == [
        ("x / v1", [2, 1]),
        ("y / v1", [3]),
        ("x / Empty", []),  # a milestone shows even with no issues loaded
    ]


def test_pinned_milestones_limit_and_order_the_milestones_ignoring_case():
    assert pinned(MILESTONES, []) == MILESTONES
    assert pinned(MILESTONES, ["a/x/empty", "a/y/v1", "a/z/v1"]) == [MILESTONES[2], MILESTONES[1]]


def test_progress_bar_fills_with_the_share_of_done_issues():
    assert progress_bar(3, 8) == "████░░░░░░ 3/8"
    assert progress_bar(0, 0) == "░░░░░░░░░░ 0/0"
    assert progress_bar(5, 5, width=4) == "████ 5/5"
