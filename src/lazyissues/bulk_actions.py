"""Bulk actions (`B`): move or assign the selected issues, confirmed before anything is sent.

Each issue is planned on its own (`bulk`), then sent the way a single one is: a move
through `Mover.send_each`, so it shows as pending and is confirmed or rejected in the
shared move tracker; an assignment through `Writer.assign_each`. The summary lists
what was done, what GitHub refused and what was skipped, and why.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static

from lazyissues import bulk
from lazyissues.bulk import Choice, Outcome
from lazyissues.forms.assign import team_first
from lazyissues.github import GitHubError
from lazyissues.menu import Menu
from lazyissues.models import Issue
from lazyissues.mover import Mover
from lazyissues.writer import Writer


class BulkModal[R](ModalScreen[R]):
    """The frame every bulk action screen shares."""

    DEFAULT_CSS = """
    BulkModal { align: center middle; }
    BulkModal > Vertical {
        width: 72; height: auto; max-height: 90%; padding: 0 1;
        background: $surface; border: wide $primary;
    }
    BulkModal .title { text-style: bold; }
    BulkModal .hint { color: $text-muted; }
    BulkModal VerticalScroll { height: auto; max-height: 20; }
    BulkModal Horizontal { height: auto; margin-top: 1; }
    BulkModal Button { margin-right: 1; }
    """


class BulkMenu(BulkModal[int | None]):
    """Dismisses with the index of the option taken, or None when cancelled."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, title: str, prompts: Sequence[str]) -> None:
        super().__init__()
        self.heading = title
        self.prompts = prompts

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(Text(self.heading), classes="title")
            yield Menu(*(Text(prompt) for prompt in self.prompts))
            yield Static("Enter chooses · Esc cancels", classes="hint")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_index)


class BulkReport(BulkModal[bool]):
    """A bulk action's outcomes, in titled sections, with buttons."""

    BUTTONS: ClassVar[list[tuple[str, str]]]  # (id, label); the first is the primary one

    def __init__(self, title: str, sections: list[tuple[str, list[str]]]) -> None:
        super().__init__()
        self.heading = title
        self.sections = sections

    def compose(self) -> ComposeResult:
        report = Text()
        for heading, lines in self.sections:
            report.append(f"{heading}\n", style="bold")
            report.append("".join(f"  {line}\n" for line in lines))
        report.rstrip()
        with Vertical():
            yield Static(Text(self.heading), classes="title")
            with VerticalScroll():
                yield Static(report, id="report")
            with Horizontal():
                for i, (id, label) in enumerate(self.BUTTONS):
                    yield Button(label, id=id, variant="primary" if i == 0 else "default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == self.BUTTONS[0][0])


class BulkConfirm(BulkReport):
    """What a bulk action will change and skip; dismisses with whether to send it."""

    BINDINGS = [
        Binding("enter,y", "dismiss(True)", "Send"),
        Binding("escape,n", "dismiss(False)", "Cancel"),
    ]
    BUTTONS = [("send", "Send"), ("cancel", "Cancel")]


class BulkSummary(BulkReport):
    BINDINGS = [Binding("enter,escape", "dismiss(True)", "Close")]
    BUTTONS = [("close", "Close")]


async def run(mover: Mover, writer: Writer, issues: list[Issue], sent: Callable[[], None]) -> None:
    """Ask what to do to `issues`, confirm it, do it and list the outcomes. `sent` runs
    once the action is confirmed."""
    app = mover.app
    count = f"{len(issues)} issue{'' if len(issues) == 1 else 's'}"
    action = await app.push_screen_wait(BulkMenu(f"{count} selected", ["Move", "Assign"]))
    if action == 0:
        planner = await mover.planner(issues)
        if planner is None:
            return
        moving = await _confirm(app, f"Move {count}", bulk.moves(planner, issues), issues)
        if moving is None:
            return
        sent()
        label = moving.choice.label
        outcomes = await mover.send_each(moving.outcomes, label)
    elif action == 1:
        assignments = await _assignments(writer, issues)
        if assignments is None:
            return
        assigning = await _confirm(app, f"Assign {count}", assignments, issues)
        if assigning is None:
            return
        sent()
        label = assigning.choice.label
        outcomes = await writer.assign_each(assigning.outcomes, assigning.choice.target)
    else:
        return
    await app.push_screen_wait(BulkSummary(label, bulk.report(outcomes, "Done")))


@dataclass(frozen=True)
class _Confirmed[T]:
    choice: Choice[T]
    outcomes: list[Outcome]  # each issue's plan, or why it's skipped


async def _confirm[T](
    app: App[Any], title: str, choices: list[Choice[T]], issues: list[Issue]
) -> _Confirmed[T] | None:
    """Offer `choices` and confirm the one taken; None when cancelled."""
    if not choices:
        app.notify("None of the selected issues can.", title=title, severity="warning")
        return None
    prompts = [f"{choice.label}  ({choice.can} of {len(issues)} can)" for choice in choices]
    index = await app.push_screen_wait(BulkMenu(title, prompts))
    if index is None:
        return None
    choice = choices[index]
    outcomes = bulk.plan(choice, issues)
    confirm = BulkConfirm(f"{choice.label}?", bulk.report(outcomes, "Will change"))
    return _Confirmed(choice, outcomes) if await app.push_screen_wait(confirm) else None


async def _assignments(writer: Writer, issues: list[Issue]) -> list[Choice[str]] | None:
    """Assigning each user assignable in some issue's repo, the team first; None when
    GitHub couldn't list them (the error shows)."""
    repos = sorted({issue.repo for issue in issues})
    try:
        users = await asyncio.gather(*(writer.github.assignable_users(repo) for repo in repos))
    except GitHubError as e:
        writer.app.notify(str(e), title="Can't assign", severity="error", timeout=10)
        return None
    logins = team_first(writer.config.team, [user for found in users for user in found])
    return bulk.assignments(logins, dict(zip(repos, users, strict=True)), issues)
