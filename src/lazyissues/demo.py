"""Made-up data for `--demo`. Never real accounts or repositories."""

from lazyissues.config import Config
from lazyissues.fake import FakeGitHub
from lazyissues.models import Issue

VIEWER = "octo-dev"
REPOS = ["octo-dev/tidepool", "octo-dev/lanternfish"]


def _issue(repo: str, number: int, title: str, *assignees: str, labels: tuple[str, ...] = ()):
    return Issue(
        repo=repo,
        number=number,
        title=title,
        url=f"https://github.com/{repo}/issues/{number}",
        assignees=assignees,
        labels=labels,
    )


def config() -> Config:
    return Config(repos=REPOS)


def github() -> FakeGitHub:
    tide, lantern = REPOS
    return FakeGitHub(
        viewer=VIEWER,
        issues=[
            _issue(tide, 12, "Sync stalls when the tide table is empty", VIEWER, labels=("bug",)),
            _issue(tide, 15, "Add a weekly digest of high tides", VIEWER, labels=("enhancement",)),
            _issue(tide, 18, "Document the import format", labels=("documentation",)),
            _issue(lantern, 4, "Lantern glow ignores the dark theme", VIEWER, "sam-reef"),
            _issue(lantern, 7, "Cache fish sightings between runs", "sam-reef"),
        ],
    )
