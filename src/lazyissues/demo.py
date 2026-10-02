"""Made-up data for `--demo`. Never real accounts or repositories."""

from lazyissues.config import Config, Repo, Status
from lazyissues.fake import FakeGitHub
from lazyissues.models import Issue

VIEWER = "octo-dev"
REPOS = ["octo-dev/tidepool", "octo-dev/lanternfish"]
PROJECT = "octo-dev/3"  # lanternfish's status source; tidepool uses status labels


def _issue(
    repo: str,
    number: int,
    title: str,
    *assignees: str,
    labels: tuple[str, ...] = (),
    project_status: str | None = None,
):
    return Issue(
        repo=repo,
        number=number,
        title=title,
        url=f"https://github.com/{repo}/issues/{number}",
        assignees=assignees,
        labels=labels,
        project_statuses={PROJECT: project_status} if project_status else {},
    )


def config() -> Config:
    tide, lantern = REPOS
    return Config(
        repos=[Repo(tide), Repo(lantern, "project", PROJECT)],
        statuses=[
            Status("Todo"),
            Status("In Progress", active=True, key="p"),
            Status("In Review"),
        ],
    )


def github() -> FakeGitHub:
    tide, lantern = REPOS
    return FakeGitHub(
        viewer=VIEWER,
        issues=[
            _issue(
                tide,
                12,
                "Sync stalls when the tide table is empty",
                VIEWER,
                labels=("bug", "in-progress"),
            ),
            _issue(
                tide,
                15,
                "Add a weekly digest of high tides",
                VIEWER,
                labels=("enhancement", "todo", "in-review"),  # two status labels
            ),
            _issue(tide, 18, "Document the import format", labels=("documentation",)),
            _issue(
                lantern,
                4,
                "Lantern glow ignores the dark theme",
                VIEWER,
                "sam-reef",
                project_status="In Progress",
            ),
            _issue(
                lantern, 7, "Cache fish sightings between runs", "sam-reef", project_status="Todo"
            ),
            _issue(lantern, 9, "Count lanterns per reef", VIEWER),  # not on the project
            _issue(lantern, 11, "Wait for the depth sensor API", VIEWER, project_status="Blocked"),
        ],
    )
