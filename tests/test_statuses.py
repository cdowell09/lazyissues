from lazyissues.config import Config, Repo, Status
from lazyissues.models import Issue
from lazyissues.statuses import DONE, NO_STATUS, IssueStatus, StatusRules, is_done_option

CONFIG = Config(
    repos=[Repo("a/labels"), Repo("a/board", "project", "a/7")],
    statuses=[Status("Todo"), Status("In Progress", active=True), Status("In Review")],
)


def issue(repo: str = "a/labels", number: int = 1, **fields) -> Issue:
    return Issue(
        repo, number, f"Issue {number}", f"https://github.com/{repo}/issues/{number}", **fields
    )


def test_a_status_label_resolves_to_the_configured_status():
    status = StatusRules(CONFIG).status_of(issue(labels=("bug", "in_progress")))
    assert (status.name, status.ambiguous) == ("In Progress", False)


def test_several_status_labels_resolve_to_the_latest_in_order_flagged_ambiguous():
    rules = StatusRules(CONFIG)
    status = rules.status_of(issue(labels=("in review", "Todo", "IN-PROGRESS")))
    assert (status.name, status.ambiguous) == ("In Review", True)
    # Two spellings of one status are still one status.
    assert not rules.status_of(issue(labels=("todo", "TODO"))).ambiguous


def test_a_project_backed_repo_reads_its_projects_status_and_ignores_labels():
    rules = StatusRules(CONFIG)
    on_board = issue("a/board", labels=("todo",), project_statuses={"a/7": "in progress"})
    assert rules.status_of(on_board) == IssueStatus("In Progress")
    other_board = issue("a/board", labels=("todo",), project_statuses={"a/8": "Todo"})
    assert rules.status_of(other_board) == IssueStatus(NO_STATUS)
    unknown = issue("a/board", project_statuses={"a/7": "Blocked"})
    assert rules.status_of(unknown) == IssueStatus("Blocked")


def test_groups_show_no_status_first_then_configured_order_then_unknown_as_first_seen():
    issues = [
        issue("a/board", 1, project_statuses={"a/7": "Blocked"}),
        issue("a/labels", 2, labels=("in-progress",)),
        issue("a/board", 3, project_statuses={"a/7": "Waiting"}),
        issue("a/labels", 4, labels=("todo",)),
        issue("a/board", 5, project_statuses={"a/7": "In Progress"}),
        issue("a/labels", 6),
        issue("a/board", 7, project_statuses={"a/7": "blocked"}),
    ]
    groups = StatusRules(CONFIG).group(issues)
    assert [(g.name, [i.number for i in g.issues]) for g in groups] == [
        (NO_STATUS, [6]),
        ("Todo", [4]),
        ("In Progress", [2, 5]),
        ("Blocked", [1, 7]),
        ("Waiting", [3]),
    ]


def test_closed_issues_group_as_done_after_every_other_status():
    issues = [
        issue("a/labels", 1, labels=("in-progress",), closed=True),
        issue("a/board", 2, project_statuses={"a/7": "Blocked"}),
        issue("a/labels", 3, labels=("todo",)),
    ]
    rules = StatusRules(CONFIG)
    assert rules.status_of(issues[0]) == IssueStatus(DONE)
    assert [(g.name, [i.number for i in g.issues]) for g in rules.group(issues)] == [
        ("Todo", [3]),
        ("Blocked", [2]),
        (DONE, [1]),
    ]


def test_done_means_closed_whatever_the_status():
    rules = StatusRules(CONFIG)
    assert rules.is_done(issue(closed=True, labels=("in-progress",)))
    assert not rules.is_done(issue(labels=("in-progress",)))


def test_active_statuses_match_by_normalized_name():
    rules = StatusRules(CONFIG)
    assert rules.is_active("in_progress")
    assert not rules.is_active("Todo")
    assert not rules.is_active("Blocked")


def test_project_options_that_mean_done():
    assert [is_done_option(n) for n in ("Done", "closed", "COMPLETED", "In Progress")] == [
        True,
        True,
        True,
        False,
    ]


def test_project_backed_repos_reach_their_projects_options_except_done():
    rules = StatusRules(CONFIG)
    options = ["Todo", "in-progress", "Blocked", "Done", "Closed"]
    assert rules.reachable(issue("a/board"), options) == ["Todo", "In Progress", "Blocked"]
    assert rules.reachable(issue("a/labels"), options) == ["Todo", "In Progress", "In Review"]


def test_status_labels_only_exist_in_label_backed_repos():
    rules = StatusRules(CONFIG)
    assert rules.status_labels(issue(labels=("bug", "todo", "In-Review"))) == ["todo", "In-Review"]
    assert rules.status_labels(issue("a/board", labels=("todo",))) == []


def test_shortcuts_map_lowercase_keys_to_statuses():
    config = Config(repos=[], statuses=[Status("Todo"), Status("In Progress", key="P")])
    assert StatusRules(config).shortcuts() == {"p": "In Progress"}
