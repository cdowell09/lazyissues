"""First-run setup: screens that edit a discovered `Proposal`, then save it as config."""

from pathlib import Path

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, SelectionList, Static

from lazyissues import config as config_module
from lazyissues.config import Config, ConfigError, Repo
from lazyissues.discovery import Proposal, discover, find_repo
from lazyissues.github import Gateway, GitHubError
from lazyissues.status_list import StatusList

BACK = Binding("escape", "app.pop_screen", "Back")

CSS = """
Screen { padding: 0 1; }
.title { text-style: bold; margin-top: 1; }
.hint { color: $text-muted; }
.warning { color: $warning; margin-top: 1; }
#add-error { color: $error; }
SelectionList { height: auto; max-height: 16; }
Horizontal { height: auto; margin-top: 1; }
Button { margin-right: 1; }
"""


class SetupApp(App[Config]):
    """Runs setup and exits with the saved config, or None if the user quits."""

    TITLE = "lazyissues setup"
    CSS = CSS
    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit without saving"),
        Binding("r", "retry", "Retry"),
    ]

    def __init__(self, github: Gateway, path: Path) -> None:
        super().__init__()
        self.github = github
        self.path = path
        self.failed = False  # discovery failed; `r` retries it

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.propose()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        return self.failed if action == "retry" else True

    def action_retry(self) -> None:
        self.propose()

    @work(exclusive=True)
    async def propose(self) -> None:
        status = self.query_one("#status", Static)
        status.update("Looking for repos where you have open issues…")
        try:
            proposal = await discover(self.github)
        except GitHubError as e:
            status.update(f"Setup couldn't read GitHub: {e}\n\nPress r to try again.")
            self.failed = True
            self.refresh_bindings()
            return
        self.failed = False
        self.refresh_bindings()
        self.push_screen(ReposScreen(proposal))

    def save(self, config: Config) -> None:
        try:
            config_module.save(config, self.path)
        except (OSError, ConfigError) as e:
            self.notify(str(e), title=f"Couldn't write {self.path}", severity="error")
            return
        self.exit(config)


class _Step(Screen[None]):
    def __init__(self, proposal: Proposal) -> None:
        super().__init__()
        self.proposal = proposal

    @property
    def setup(self) -> SetupApp:
        assert isinstance(self.app, SetupApp)
        return self.app

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll():
            yield from self.body()
        yield Footer()

    def body(self) -> ComposeResult:
        raise NotImplementedError


class ReposScreen(_Step):
    def body(self) -> ComposeResult:
        if self.proposal.missing_project_scope:
            yield Static(
                "Your `gh` token lacks the `project` scope, so repos can only use labels for"
                " statuses. To use project boards, quit, run `gh auth refresh -s project`,"
                " and start lazyissues again.",
                id="scope-warning",
                classes="warning",
            )
        if self.proposal.unreadable:
            yield Static(
                "Left out because GitHub wouldn't read them (add one below to retry):\n"
                + "\n".join(f"{repo}: {error}" for repo, error in self.proposal.unreadable.items()),
                classes="warning",
            )
        yield Label("Repos to track", classes="title")
        yield Static("Suggested from repos where you have open issues.", classes="hint")
        yield SelectionList[str](
            *((repo, repo, repo in self.proposal.repos) for repo in self.proposal.offers)
        )
        yield Input(placeholder="Add a repo: owner/name, then Enter")
        yield Static(id="add-error")
        with Horizontal():
            yield Button("Next", id="next", variant="primary", action="screen.next")

    @on(SelectionList.SelectedChanged)
    def choose_repos(self, event: SelectionList.SelectedChanged) -> None:
        self.proposal.choose_repos(event.selection_list.selected)

    @on(Input.Submitted)
    def add_repo(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        if not config_module.is_repo_name(name):
            self.query_one("#add-error", Static).update("Enter a repo as owner/name.")
            return
        self.find(name)

    @work(exclusive=True)
    async def find(self, name: str) -> None:
        error = self.query_one("#add-error", Static)
        try:
            offer = await find_repo(
                self.setup.github, name, read_projects=not self.proposal.missing_project_scope
            )
        except GitHubError as e:
            error.update(str(e))
            return
        error.update("")
        self.query_one(Input).clear()
        name = self.proposal.add(name, offer)
        repos = self.query_one(SelectionList)
        if name in [repos.get_option_at_index(i).value for i in range(repos.option_count)]:
            repos.select(name)
        else:
            repos.add_option((name, name, True))

    def action_next(self) -> None:
        if not self.proposal.repos:
            self.notify("Pick at least one repo.", severity="warning")
            return
        self.app.push_screen(SourcesScreen(self.proposal))


class SourcesScreen(_Step):
    """Each repo's status source and, for label-backed repos, which labels are statuses."""

    BINDINGS = [BACK]

    def body(self) -> ComposeResult:
        yield Label("Status sources", classes="title")
        yield Static(
            "Each repo keeps its statuses in labels or in one project's Status field."
            " For label-backed repos, check the labels that are statuses.",
            classes="hint",
        )
        for repo in self.proposal.repos:
            offer = self.proposal.offers[repo]
            source = self.proposal.sources[repo]
            yield Label(repo, classes="title")
            yield Select[Repo](
                [
                    ("Labels", Repo(repo)),
                    *(
                        (f"Project: {p.title} ({p.ref})", Repo(repo, "project", p.ref))
                        for p in offer.projects
                    ),
                ],
                value=source,
                allow_blank=False,
            )
            picked = self.proposal.status_labels[repo]
            labels = SelectionList[str](
                *((label, label, label in picked) for label in offer.labels), name=repo
            )
            labels.display = source.project is None
            yield labels
        with Horizontal():
            yield Button("Next", id="next", variant="primary", action="screen.next")

    @on(Select.Changed)
    def choose_source(self, event: Select.Changed) -> None:
        source = event.value
        assert isinstance(source, Repo)
        self.proposal.set_source(source)
        for labels in self.query(SelectionList):
            if labels.name == source.name:
                labels.display = source.project is None

    @on(SelectionList.SelectedChanged)
    def pick_labels(self, event: SelectionList.SelectedChanged) -> None:
        repo = event.selection_list.name
        assert repo is not None
        self.proposal.pick_labels(repo, event.selection_list.selected)

    def action_next(self) -> None:
        self.app.push_screen(StatusesScreen(self.proposal))


class StatusesScreen(_Step):
    """The combined status list: its order, and which statuses are active."""

    BINDINGS = [BACK, Binding("ctrl+s", "save", "Save")]

    def body(self) -> ComposeResult:
        statuses = self.proposal.statuses
        yield Label("Statuses", classes="title")
        yield Static(
            "Groups show in this order. Check the active statuses: moving an issue to one"
            " assigns you if nobody is.",
            classes="hint",
        )
        status_list = StatusList(statuses)
        status_list.display = bool(statuses)
        yield status_list
        empty = Static(
            "No statuses yet. Issues show under No status until you add some to config.toml.",
            classes="hint",
        )
        empty.display = not statuses
        yield empty
        with Horizontal():
            yield Button("Move up", action="screen.move(-1)")
            yield Button("Move down", action="screen.move(1)")
            yield Button("Save", id="save", variant="primary", action="screen.save")
        yield Static(
            f"Your team roster starts with {self.proposal.viewer}, and the Filters tab with"
            " Ready for me and Needs triage.",
            classes="hint",
        )

    @on(StatusList.Changed)
    def edit_statuses(self, event: StatusList.Changed) -> None:
        self.proposal.set_statuses(event.statuses)

    def action_move(self, step: int) -> None:
        self.query_one(StatusList).action_move(step)

    def action_save(self) -> None:
        self.setup.save(self.proposal.config())
