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


def first_run(viewer: str) -> Config:
    """The config a first setup builds on: `viewer` on the roster, and the starter filters."""
    return Config(repos=[], statuses=[], team=[viewer], filters=list(STARTER_FILTERS))


@dataclass
class Proposal:
    """A config being edited during setup, from `base`. Statuses follow from the sources
    chosen; everything else setup doesn't edit (roster, filters, preferences) is `base`'s."""

    base: Config
    missing_project_scope: bool = False
    unreadable: dict[str, str] = field(default_factory=dict)  # repos left out, with the error
    offers: dict[str, RepoOffer] = field(default_factory=dict)  # every repo found or added
    repos: list[str] = field(default_factory=list)  # the repo set, in `offers` order
    sources: dict[str, Repo] = field(default_factory=dict)  # each repo's status source
    status_labels: dict[str, list[str]] = field(default_factory=dict)  # labels that are statuses
    order: list[str] = field(default_factory=list)  # status names as the user ordered them
    active: set[str] = field(default_factory=lambda: {normalize("In Progress")})

    @property
    def kept(self) -> list[Repo]:
        """`base`'s repos setup can't read, which it keeps as they are."""
        return [repo for repo in self.base.repos if repo.name in self.unreadable]

    @property
    def has_repos(self) -> bool:
        """Whether the config would track a repo: one chosen, or one kept."""
        return bool(self.repos or self.kept)

    def seed(self) -> None:
        """Start from `base`'s repos, status sources and statuses."""
        self.choose_repos(self.base.repo_names)
        statuses = {normalize(status.name) for status in self.base.statuses}
        for repo in self.base.repos:
            if repo.name in self.offers:
                self.set_source(repo)
                self.status_labels[repo.name] = [
                    label for label in self.offers[repo.name].labels if normalize(label) in statuses
                ]
        self.order = [status.name for status in self.base.statuses]
        self.active = {normalize(status.name) for status in self.base.statuses if status.active}

    def add(self, repo: str, offer: RepoOffer) -> str:
        """Add `repo` to the repo set and return its name as listed.

        Names that differ only in case are one repo, as on GitHub. A repo uses
        its project for statuses when it is linked to exactly one, else labels.
        """
        repo = next((known for known in self.offers if known.casefold() == repo.casefold()), repo)
        self.unreadable = {
            name: error
            for name, error in self.unreadable.items()
            if name.casefold() != repo.casefold()
        }
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
        """Take `source` for its repo's statuses; labels if GitHub no longer offers its project."""
        offered = [None, *(project.ref for project in self.offers[source.name].projects)]
        self.sources[source.name] = source if source.project in offered else Repo(source.name)

    def pick_labels(self, repo: str, selected: Collection[str]) -> None:
        """Make the `selected` labels the repo's status labels."""
        self.status_labels[repo] = [
            label for label in self.offers[repo].labels if label in selected
        ]

    @property
    def statuses(self) -> list[Status]:
        """Every status the chosen sources offer: user-ordered first, then as found."""
        names = [name for repo in self.repos for name in self._offered_by(repo)]
        if self.kept:  # which statuses a repo setup can't read uses is unknown: keep them all
            names += [status.name for status in self.base.statuses]
        offered: dict[str, str] = {}
        for name in names:
            offered.setdefault(normalize(name), name)
        # `base`'s spelling of each status and its move key stand.
        kept = {normalize(s.name): s for s in self.base.statuses}
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
        """`base` with the repos and statuses chosen; a kept repo stays where it was."""
        if not self.has_repos:
            raise ValueError("A config needs at least one repo.")
        place = {name: at for at, name in enumerate(self.base.repo_names)}
        repos = [*(self.sources[repo] for repo in self.repos), *self.kept]
        repos.sort(key=lambda repo: place.get(repo.name, len(place)))
        return replace(self.base, repos=repos, statuses=self.statuses)


async def find_repo(github: Gateway, repo: str, *, read_projects: bool) -> RepoOffer:
    """What `repo` offers; its projects only when the token may read them."""
    labels = await github.repo_labels(repo)
    projects = await github.repo_projects(repo) if read_projects else []
    return RepoOffer(tuple(labels), tuple(projects))


async def discover(github: Gateway, current: Config | None = None) -> Proposal:
    """Propose a config from the repos where the viewer has open issues,
    or, on a rerun, from the `current` config with those repos offered too.

    A `current` repo setup can't read, or whose project it may not read, is kept as is."""
    (viewer, scopes), issues = await asyncio.gather(
        github.whoami(), github.search_issues(OPEN_INVOLVING_ME)
    )
    read_projects = "project" in scopes
    proposal = Proposal(current or first_run(viewer), missing_project_scope=not read_projects)
    if not read_projects:
        for repo in proposal.base.repos:
            if repo.project is not None:
                proposal.unreadable[repo.name] = "reading its project needs the project scope"
    repos = [repo for repo in proposal.base.repo_names if repo not in proposal.unreadable]
    known = {repo.casefold() for repo in proposal.base.repo_names}
    repos += sorted({issue.repo for issue in issues if issue.repo.casefold() not in known})
    offers = await asyncio.gather(
        *(find_repo(github, repo, read_projects=read_projects) for repo in repos),
        return_exceptions=True,
    )
    for repo, offer in zip(repos, offers, strict=True):
        if isinstance(offer, GitHubError):
            proposal.unreadable[repo] = str(offer)  # one repo's error shouldn't stop setup
        elif isinstance(offer, BaseException):
            raise offer
        else:
            proposal.add(repo, offer)
    if current is not None:
        proposal.seed()
    return proposal
