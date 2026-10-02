import pytest

from lazyissues.config import Config, Repo, Status
from lazyissues.fake import FakeGitHub
from lazyissues.models import CloseReason, Issue, Project
from lazyissues.move_planner import (
    AddLabel,
    AddToProject,
    Assign,
    Close,
    MoveTo,
    Planner,
    RemoveLabels,
    Reopen,
    SetProjectStatus,
    Skip,
    Step,
    Target,
    apply,
    duplicate_key,
    send,
)
from lazyissues.statuses import StatusRules

CONFIG = Config(
    repos=[Repo("o/labels"), Repo("o/board", "project", "o/1")],
    statuses=[Status("Todo"), Status("In Progress", active=True, key="p"), Status("In Review")],
)
# The project's Status options in board order, as GitHub spells them.
OPTIONS = {"o/1": ["Todo", "In progress", "Blocked", "Done"]}
CLOSES = [
    Close(CloseReason.COMPLETED),
    Close(CloseReason.NOT_PLANNED),
    Close(CloseReason.DUPLICATE),
]


def issue(repo: str = "o/labels", number: int = 1, **fields) -> Issue:
    fields.setdefault("assignees", ("sam",))
    return Issue(
        repo, number, f"Issue {number}", f"https://github.com/{repo}/issues/{number}", **fields
    )


def planner() -> Planner:
    return Planner(StatusRules(CONFIG), viewer="me", project_options=OPTIONS)


def planned(moving: Issue, target: Target) -> list[Step]:
    plan = planner().plan(moving, target)
    assert isinstance(plan, list), plan
    return plan


def test_a_label_backed_issue_can_reach_every_configured_status_or_close():
    assert planner().targets(issue()) == [
        MoveTo("Todo"),
        MoveTo("In Progress"),
        MoveTo("In Review"),
        *CLOSES,
    ]


def test_a_project_backed_issue_can_reach_its_projects_options_except_done():
    # A matching option shows under the configured name; the rest as the project has them.
    assert planner().targets(issue("o/board")) == [
        MoveTo("Todo"),
        MoveTo("In Progress"),
        MoveTo("Blocked"),
        *CLOSES,
    ]


def test_a_closed_issue_offers_reopen_instead_of_closing():
    targets = planner().targets(issue(closed=True))
    assert targets[-1] == Reopen()
    assert not any(isinstance(target, Close) for target in targets)


def test_a_label_move_adds_the_target_and_removes_every_other_status_label():
    moving = issue(labels=("bug", "todo", "In-Review"))
    assert planner().plan(moving, MoveTo("In Progress")) == [
        AddLabel("In Progress"),
        RemoveLabels(("todo", "In-Review")),
    ]


def test_a_label_move_keeps_a_label_already_spelling_the_target():
    moving = issue(labels=("in_review", "todo"))
    assert planner().plan(moving, MoveTo("In Review")) == [RemoveLabels(("todo",))]


def test_moving_to_the_status_an_issue_already_has_is_skipped():
    assert planner().plan(issue(labels=("todo",)), MoveTo("todo")) == Skip("Already in Todo")
    on_board = issue("o/board", project_statuses={"o/1": "In progress"})
    assert planner().plan(on_board, MoveTo("In Progress")) == Skip("Already in In Progress")


def test_a_status_the_source_cant_reach_is_skipped():
    assert planner().plan(issue(), MoveTo("Blocked")) == Skip("o/labels has no status Blocked")
    assert planner().plan(issue("o/board"), MoveTo("In Review")) == Skip(
        "Project o/1 has no status In Review"
    )


def test_a_project_move_adds_the_issue_to_the_project_when_it_isnt_on_it():
    assert planner().plan(issue("o/board"), MoveTo("Blocked")) == [
        AddToProject("o/1"),
        SetProjectStatus("o/1", "Blocked"),
    ]


def test_a_project_move_only_sets_the_status_when_the_issue_is_on_the_project():
    # GitHub may spell the project's owner differently from the config.
    on_board = issue("o/board", project_statuses={"O/1": "Todo"})
    assert planner().plan(on_board, MoveTo("Blocked")) == [SetProjectStatus("o/1", "Blocked")]


def test_moving_an_unassigned_issue_to_an_active_status_assigns_me():
    plan = planner().plan(issue(assignees=()), MoveTo("In Progress"))
    assert plan == [AddLabel("In Progress"), Assign("me")]
    assert planner().plan(issue(assignees=()), MoveTo("Todo")) == [AddLabel("Todo")]


def test_moving_a_closed_issue_to_a_status_reopens_it():
    closed = issue(closed=True, labels=("todo",))
    assert planner().plan(closed, MoveTo("In Review")) == [
        Reopen(),
        AddLabel("In Review"),
        RemoveLabels(("todo",)),
    ]
    assert planner().plan(closed, MoveTo("Todo")) == [Reopen()]


def test_closing_and_reopening():
    p = planner()
    assert p.plan(issue(), Close(CloseReason.NOT_PLANNED)) == [Close(CloseReason.NOT_PLANNED)]
    assert p.plan(issue(closed=True), Close(CloseReason.COMPLETED)) == Skip("Already closed")
    assert p.plan(issue(closed=True), Reopen()) == [Reopen()]
    assert p.plan(issue(), Reopen()) == Skip("Already open")


def test_closing_as_a_duplicate_needs_another_issue():
    p = planner()
    duplicate = Close(CloseReason.DUPLICATE, "o/labels#2")
    assert p.plan(issue(), duplicate) == [duplicate]
    assert p.plan(issue(), Close(CloseReason.DUPLICATE)) == Skip("Choose the issue it duplicates")
    assert p.plan(issue(), Close(CloseReason.DUPLICATE, "o/labels#1")) == Skip(
        "An issue can't duplicate itself"
    )


def test_a_plan_applied_to_the_issue_gives_the_status_github_will_have():
    rules = StatusRules(CONFIG)
    moving = issue(assignees=(), labels=("bug", "todo"), closed=True)
    moved = apply(planned(moving, MoveTo("In Progress")), moving)
    assert rules.status_of(moved).name == "In Progress"
    assert (moved.labels, moved.assignees, moved.closed) == (("bug", "In Progress"), ("me",), False)

    on_board = issue("o/board", project_statuses={"O/1": "Todo", "o/2": "Done"})
    moved = apply(planned(on_board, MoveTo("Blocked")), on_board)
    assert moved.project_statuses == {"o/1": "Blocked", "o/2": "Done"}

    closing = [Close(CloseReason.COMPLETED)]
    assert apply(closing, issue()).closed


def test_applying_a_plan_twice_changes_nothing_more():
    moving = issue(assignees=(), labels=("todo",))
    plan = planned(moving, MoveTo("In Progress"))
    once = apply(plan, moving)
    assert apply(plan, once) == once


def test_the_duplicate_prompt_accepts_numbers_and_references():
    moving = issue("o/labels", 1)
    assert duplicate_key(moving, "12") == "o/labels#12"
    assert duplicate_key(moving, " #12 ") == "o/labels#12"
    assert duplicate_key(moving, "board#3") == "o/board#3"
    assert duplicate_key(moving, "acme/web#3") == "acme/web#3"
    assert duplicate_key(moving, "https://github.com/acme/web/issues/3") == "acme/web#3"
    assert duplicate_key(moving, "soon") is None


def github() -> FakeGitHub:
    return FakeGitHub(
        viewer="me",
        issues=[
            issue("o/labels", 1, assignees=(), labels=("bug", "todo")),
            issue("o/labels", 2, closed=True, labels=("in-review",)),
            issue("o/board", 3, assignees=(), project_statuses={"o/1": "Todo"}),
            issue("o/board", 4),  # not on the project
        ],
        projects={"o/board": [Project("o/1", "Board", tuple(OPTIONS["o/1"]))]},
    )


@pytest.mark.parametrize(
    ("number", "target"),
    [
        (1, MoveTo("In Progress")),
        (1, Close(CloseReason.COMPLETED)),
        (1, Close(CloseReason.NOT_PLANNED)),
        (1, Close(CloseReason.DUPLICATE, "o/board#3")),
        (2, Reopen()),
        (2, MoveTo("Todo")),
        (3, MoveTo("In Progress")),
        (4, MoveTo("Blocked")),
    ],
)
async def test_each_move_kind_leaves_github_as_planned(number, target):
    fake = github()
    [moving] = [i for i in fake.issues if i.number == number]
    plan = planned(moving, target)

    await send(fake, moving, plan)

    [after] = [i for i in await fake.search_issues("is:issue") if i.number == number]
    expected = apply(plan, moving)
    rules = StatusRules(CONFIG)
    assert rules.status_of(after) == rules.status_of(expected)
    assert (after.closed, after.assignees) == (expected.closed, expected.assignees)
