"""Bulk planning: what a bulk action offers, and what it does to each selected issue."""

from lazyissues import bulk
from lazyissues.config import Config, Repo, Status
from lazyissues.models import Issue
from lazyissues.move_planner import Assign, Planner, Skip
from lazyissues.statuses import StatusRules

CONFIG = Config(
    repos=[Repo("o/labels"), Repo("o/board", "project", "o/1")],
    statuses=[Status("Todo"), Status("In Progress", active=True), Status("In Review")],
)
OPTIONS = {"o/1": ["Todo", "In progress", "Blocked", "Done"]}


def issue(repo: str, number: int, **fields) -> Issue:
    fields.setdefault("assignees", ("sam",))
    return Issue(
        repo, number, f"Issue {number}", f"https://github.com/{repo}/issues/{number}", **fields
    )


TODO = issue("o/labels", 1, labels=("todo",))
ON_BOARD = issue("o/board", 2, project_statuses={"o/1": "In progress"})
CLOSED = issue("o/labels", 3, labels=("in-review",), closed=True)


def planner() -> Planner:
    return Planner(StatusRules(CONFIG), viewer="me", project_options=OPTIONS)


def test_bulk_move_offers_every_target_some_issue_can_make_with_how_many_can():
    offered = bulk.moves(planner(), [TODO, ON_BOARD, CLOSED])
    assert [(choice.label, choice.can) for choice in offered] == [
        ("Move to Todo", 2),  # 1 is already there
        ("Move to In Progress", 2),  # 2 is already there
        ("Move to In Review", 2),  # the board has no In Review
        ("Move to Blocked", 1),  # only the board has it
        ("Close as completed", 2),  # 3 is already closed
        ("Close as not planned", 2),
        ("Reopen", 1),
    ]  # no "Close as duplicate of…": a bulk move can't name each issue's original


def test_bulk_assign_skips_issues_already_assigned_or_in_repos_the_login_cant_join():
    shared = issue("o/labels", 4, assignees=("Sam", "me"))
    assignable = {"o/labels": ["me", "sam", "Kim"], "o/board": ["me", "sam"]}
    offered = bulk.assignments(["me", "sam", "kim"], assignable, [TODO, shared, ON_BOARD])
    # Every issue has sam already (logins ignore case), so there is no "Assign to sam".
    assert [(choice.label, choice.can) for choice in offered] == [
        ("Assign to me", 2),
        ("Assign to kim", 2),
    ]
    me, kim = offered
    assert me.plan(shared) == Skip("Already assigned to me")
    assert me.plan(TODO) == [Assign("me")]
    assert kim.plan(ON_BOARD) == Skip("kim can't be assigned in o/board")


def test_the_report_lists_what_was_sent_what_github_refused_and_what_was_skipped():
    [review] = [
        c for c in bulk.moves(planner(), [TODO, ON_BOARD]) if c.label == "Move to In Review"
    ]
    planned = bulk.plan(review, [TODO, ON_BOARD, CLOSED])  # listed by repo, then number
    assert bulk.report(planned, "Will move to In Review") == [
        ("Will move to In Review (2)", ["labels#1  Issue 1", "labels#3  Issue 3"]),
        ("Skipped (1)", ["board#2  Project o/1 has no status In Review"]),
    ]

    sent = [planned[1], bulk.Outcome(CLOSED, planned[2].plan, "Label permission denied")]
    assert bulk.report(sent, "Moved to In Review") == [
        ("Moved to In Review (1)", ["labels#1  Issue 1"]),
        ("Failed (1)", ["labels#3  Label permission denied"]),
    ]
