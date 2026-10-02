from lazyissues.config import Config, Repo, Status
from lazyissues.models import Issue
from lazyissues.statuses import DONE, NO_STATUS, StatusRules
from lazyissues.view_model import (
    ViewState,
    by_assignee,
    by_status,
    focusable_statuses,
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
        (g.name, g.total, [i.number for i in g.rows])
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
        (g.name, [i.number for i in g.rows])
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
