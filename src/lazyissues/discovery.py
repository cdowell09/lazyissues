"""First-run discovery: what GitHub offers, and the config setup proposes from it.

Setup's screens only edit a `Proposal`; every rule about which repos, status
sources and statuses to suggest lives here, testable against `FakeGitHub`.
"""

import asyncio
from collections.abc import Collection
from dataclasses import dataclass, field

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
        rank = {normalize(name): i for i, name in enumerate(self.order)}
        names = sorted(offered.values(), key=lambda name: rank.get(normalize(name), len(rank)))
        return [Status(name, active=normalize(name) in self.active) for name in names]

    def _offered_by(self, repo: str) -> list[str]:
        ref = self.sources[repo].project
        if ref is None:
            return self.status_labels[repo]
        (project,) = (p for p in self.offers[repo].projects if p.ref == ref)
        return [name for name in project.status_options if not is_done_option(name)]

    def move(self, name: str, step: int) -> None:
        """Move status `name` `step` places later (earlier when negative)."""
        names = [status.name for status in self.statuses]
        i = names.index(name)
        names.insert(max(0, i + step), names.pop(i))
        self.order = names

    def toggle_active(self, name: str) -> None:
        self.active ^= {normalize(name)}

    def config(self) -> Config:
        if not self.repos:
            raise ValueError("A config needs at least one repo.")
        return Config(
            repos=[self.sources[repo] for repo in self.repos],
            statuses=self.statuses,
            team=[self.viewer],
            filters=list(STARTER_FILTERS),
        )


async def find_repo(github: Gateway, repo: str, *, read_projects: bool) -> RepoOffer:
    """What `repo` offers; its projects only when the token may read them."""
    labels = await github.repo_labels(repo)
    projects = await github.repo_projects(repo) if read_projects else []
    return RepoOffer(tuple(labels), tuple(projects))


async def discover(github: Gateway) -> Proposal:
    """Propose a config from the repos where the viewer has open issues."""
    (viewer, scopes), issues = await asyncio.gather(
        github.whoami(), github.search_issues(OPEN_INVOLVING_ME)
    )
    read_projects = "project" in scopes
    repos = sorted({issue.repo for issue in issues})
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
    return proposal
