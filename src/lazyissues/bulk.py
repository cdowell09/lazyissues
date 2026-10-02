"""Bulk planning: the choices a bulk action offers, and each selected issue's plan or skip.

Pure. A bulk move plans each issue with `Planner.plan`, as a single move does; a bulk
assign plans an `Assign` of one login. `report` lists the outcomes, before sending and
after.
"""

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from functools import partial

from lazyissues.models import Issue
from lazyissues.move_planner import Assign, MoveTo, Planner, Skip, Step, Target

type Plan = list[Step] | Skip


@dataclass(frozen=True)
class Choice[T]:
    """One thing a bulk action can do, and how many of the selected issues it plans for."""

    target: T  # a move's `Target`, or the login to assign
    label: str  # "Move to In Review", "Close as completed", "Assign to sam"
    plan: Callable[[Issue], Plan]
    can: int


@dataclass(frozen=True)
class Outcome:
    issue: Issue
    plan: Plan
    error: str | None = None  # GitHub's, when it refused the plan


def moves(planner: Planner, issues: Sequence[Issue]) -> list[Choice[Target]]:
    """Every move some issue can make: statuses first, then closing and reopening."""
    targets = dict.fromkeys(target for issue in issues for target in planner.targets(issue))
    ordered = sorted(targets, key=lambda target: not isinstance(target, MoveTo))
    return _offered(
        issues,
        [
            (
                target,
                f"Move to {target.label}" if isinstance(target, MoveTo) else target.label,
                partial(planner.plan, target=target),
            )
            for target in ordered
        ],
    )


def assignments(
    logins: Sequence[str], assignable: Mapping[str, Collection[str]], issues: Sequence[Issue]
) -> list[Choice[str]]:
    """Adding each of `logins` to the issues' assignees, where `assignable` (each repo's
    assignable users) has them."""
    return _offered(
        issues,
        [(login, f"Assign to {login}", partial(_assign, login, assignable)) for login in logins],
    )


def plan(choice: Choice, issues: Sequence[Issue]) -> list[Outcome]:
    """Each issue's plan for `choice`, or why it's skipped, in repo and number order."""
    ordered = sorted(issues, key=lambda issue: (issue.repo, issue.number))
    return [Outcome(issue, choice.plan(issue)) for issue in ordered]


def report(outcomes: Sequence[Outcome], done: str) -> list[tuple[str, list[str]]]:
    """Sections listing `outcomes`: under `done` those sent (or to send), then those GitHub
    refused, with its error, then those skipped, with the reason. Empty ones are left out."""
    sent, failed, skipped = [], [], []
    for outcome in outcomes:
        ref = outcome.issue.ref
        if isinstance(outcome.plan, Skip):
            skipped.append(f"{ref}  {outcome.plan.reason}")
        elif outcome.error is not None:
            failed.append(f"{ref}  {outcome.error}")
        else:
            sent.append(f"{ref}  {outcome.issue.title}")
    sections = [(done, sent), ("Failed", failed), ("Skipped", skipped)]
    return [(f"{heading} ({len(lines)})", lines) for heading, lines in sections if lines]


def _assign(login: str, assignable: Mapping[str, Collection[str]], issue: Issue) -> Plan:
    # GitHub logins ignore case.
    if login.casefold() in {assignee.casefold() for assignee in issue.assignees}:
        return Skip(f"Already assigned to {login}")
    if login.casefold() not in {user.casefold() for user in assignable.get(issue.repo, ())}:
        return Skip(f"{login} can't be assigned in {issue.repo}")
    return [Assign(login)]


def _offered[T](
    issues: Sequence[Issue], choices: list[tuple[T, str, Callable[[Issue], Plan]]]
) -> list[Choice[T]]:
    """Each of `choices` (target, label, plan) with how many of `issues` it plans for,
    leaving out those none can take."""
    counted = [
        Choice(target, label, plan, sum(not isinstance(plan(issue), Skip) for issue in issues))
        for target, label, plan in choices
    ]
    return [choice for choice in counted if choice.can]
