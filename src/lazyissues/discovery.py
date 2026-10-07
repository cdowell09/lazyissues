"""First-run discovery: what GitHub offers, and the config setup proposes from it.

Setup's screens only edit a `Proposal`; every rule about which repos, status
sources and statuses to suggest lives here, testable against `FakeGitHub`.
"""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass, field, replace

from lazyissues.config import Config, Repo, SavedFilter, Status
from lazyissues.github import Gateway, GitHubError
from lazyissues.models import Project
from lazyissues.statuses import is_done_option, normalize

OPEN_INVOLVING_ME = "is:issue is:open involves:@me"
STARTER_FILTERS = (
    SavedFilter("Ready for me", "label:ready-for-human"),
    SavedFilter("Needs triage", "label:needs-triage"),
)


@dataclass(frozen=True)
class RepoOffer:
    """The status sources one repository offers."""

    labels: tuple[str, ...]
    projects: tuple[Project, ...]  # open projects linked to the repo


@dataclass
class Proposal:
    """A config being edited during setup. Statuses follow from the sources chosen."""

    viewer: str
    missing_project_scope: bool = False
    unreadable: dict[str, str] = field(default_factory=dict)  # repos left out, with the error
    offers: dict[str, RepoOffer] = field(default_factory=dict)  # every repo found or added
    repos: list[str] = field(default_factory=list)  # the repo set, in `offers` order
    sources: dict[str, Repo] = field(default_factory=dict)  # each repo's status source
    status_labels: dict[str, list[str]] = field(default_factory=dict)  # labels that are statuses
    order: list[str] = field(default_factory=list)  # status names as the user ordered them
    active: set[str] = field(default_factory=lambda: {normalize("In Progress")})
    current: Config | None = None  # the config a rerun started from; it keeps the rest

    def seed(self, config: Config) -> None:
        """Start from `config`: its repos, status sources, statuses and roster.

        Setup doesn't edit filters, preferences or other settings, so they carry over.
        """
        self.current = config
        self.choose_repos(config.repo_names)
        statuses = {normalize(status.name) for status in config.statuses}
        for repo in config.repos:
            offer = self.offers.get(repo.name)
            if offer is None:
                continue  # GitHub wouldn't read it; listed in `unreadable`
            if repo.project is None or repo.project in [p.ref for p in offer.projects]:
                self.sources[repo.name] = repo
            self.status_labels[repo.name] = [
                label for label in offer.labels if normalize(label) in statuses
            ]
        self.order = [status.name for status in config.statuses]
        self.active = {normalize(status.name) for status in config.statuses if status.active}

    def add(self, repo: str, offer: RepoOffer) -> str:
        """Add `repo` to the repo set and return its name as listed.

        Names that differ only in case are one repo, as on GitHub. A repo uses
        its project for statuses when it is linked to exactly one, else labels.
        """
        repo = next((known for known in self.offers if known.casefold() == repo.casefold()), repo)
        self.offers[repo] = offer
        self.choose_repos([*self.repos, repo])
        refs = [project.ref for project in offer.projects]
        source = self.sources.get(repo)
        if source is None or (source.project is not None and source.project not in refs):
            self.sources[repo] = Repo(repo, "project", refs[0]) if len(refs) == 1 else Repo(repo)
        self.status_labels.setdefault(repo, [])
        return repo

    def choose_repos(self, selected: Collection[str]) -> None:
        self.repos = [repo for repo in self.offers if repo in selected]

    def set_source(self, source: Repo) -> None:
        offered = [project.ref for project in self.offers[source.name].projects]
        assert source.project is None or source.project in offered, source
        self.sources[source.name] = source

    def pick_labels(self, repo: str, selected: Collection[str]) -> None:
        """Make the `selected` labels the repo's status labels."""
        self.status_labels[repo] = [
            label for label in self.offers[repo].labels if label in selected
        ]

    @property
    def statuses(self) -> list[Status]:
        """Every status the chosen sources offer: user-ordered first, then as found."""
        offered: dict[str, str] = {}
        for repo in self.repos:
            for name in self._offered_by(repo):
                offered.setdefault(normalize(name), name)
        # A rerun keeps the config's spelling of each status and its move key.
        kept = {normalize(s.name): s for s in self.current.statuses} if self.current else {}
        offered.update((name, kept[name].name) for name in offered.keys() & kept.keys())
        rank = {normalize(name): i for i, name in enumerate(self.order)}
        names = sorted(offered.values(), key=lambda name: rank.get(normalize(name), len(rank)))
        return [
            Status(
                name,
                active=normalize(name) in self.active,
                key=kept[normalize(name)].key if normalize(name) in kept else None,
            )
            for name in names
        ]

    def _offered_by(self, repo: str) -> list[str]:
        ref = self.sources[repo].project
        if ref is None:
            return self.status_labels[repo]
        (project,) = (p for p in self.offers[repo].projects if p.ref == ref)
        return [name for name in project.status_options if not is_done_option(name)]

    def set_statuses(self, statuses: list[Status]) -> None:
        """Take the order and active statuses the user chose from `statuses`.

        A status not listed (its source isn't chosen now) keeps whether it is active.
        """
        listed = {normalize(status.name) for status in statuses}
        self.order = [status.name for status in statuses]
        self.active = (self.active - listed) | {
            normalize(status.name) for status in statuses if status.active
        }

    def config(self) -> Config:
        if not self.repos:
            raise ValueError("A config needs at least one repo.")
        repos = [self.sources[repo] for repo in self.repos]
        if self.current is not None:
            return replace(self.current, repos=repos, statuses=self.statuses)
        return Config(
            repos=repos,
            statuses=self.statuses,
            team=[self.viewer],
            filters=list(STARTER_FILTERS),
        )


async def find_repo(github: Gateway, repo: str, *, read_projects: bool) -> RepoOffer:
    """What `repo` offers; its projects only when the token may read them."""
    labels = await github.repo_labels(repo)
    projects = await github.repo_projects(repo) if read_projects else []
    return RepoOffer(tuple(labels), tuple(projects))


async def discover(github: Gateway, current: Config | None = None) -> Proposal:
    """Propose a config from the repos where the viewer has open issues,
    or, on a rerun, from the `current` config with those repos offered too."""
    (viewer, scopes), issues = await asyncio.gather(
        github.whoami(), github.search_issues(OPEN_INVOLVING_ME)
    )
    read_projects = "project" in scopes
    repos = current.repo_names if current else []
    known = {repo.casefold() for repo in repos}
    repos += sorted({issue.repo for issue in issues if issue.repo.casefold() not in known})
    offers = await asyncio.gather(
        *(find_repo(github, repo, read_projects=read_projects) for repo in repos),
        return_exceptions=True,
    )
    proposal = Proposal(viewer, missing_project_scope=not read_projects)
    for repo, offer in zip(repos, offers, strict=True):
        if isinstance(offer, GitHubError):
            proposal.unreadable[repo] = str(offer)  # one repo's error shouldn't stop setup
        elif isinstance(offer, BaseException):
            raise offer
        else:
            proposal.add(repo, offer)
    if current is not None:
        proposal.seed(current)
    return proposal
