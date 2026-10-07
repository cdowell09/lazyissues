"""Made-up data for `--demo`. Never real accounts or repositories."""

from datetime import UTC, datetime, timedelta

from lazyissues.config import Config, Repo, SavedFilter, Status
from lazyissues.discovery import STARTER_FILTERS
from lazyissues.fake import FakeGitHub
from lazyissues.models import Event, Issue, IssueDetail, Project, ProjectField

VIEWER = "octo-dev"
TEAM = ["sam-reef", "mo-kelp"]  # the viewer isn't listed; Team adds them
REPOS = ["octo-dev/tidepool", "octo-dev/lanternfish"]
PROJECT = "octo-dev/3"  # lanternfish's status source; tidepool uses status labels
COUNTING = "octo-dev/lanternfish#9"  # a parent issue


def _issue(
    repo: str,
    number: int,
    title: str,
    *assignees: str,
    updated: timedelta,  # how long ago it last changed
    labels: tuple[str, ...] = (),
    project_status: str | None = None,
    closed_days_ago: int | None = None,
    milestone: str | None = None,
    parent: str | None = None,
):
    now = datetime.now(UTC)
    closed_at = None
    if closed_days_ago is not None:
        closed_at = (now - timedelta(days=closed_days_ago)).isoformat()
    return Issue(
        repo=repo,
        number=number,
        title=title,
        url=f"https://github.com/{repo}/issues/{number}",
        assignees=assignees,
        labels=labels,
        project_statuses={PROJECT: project_status} if project_status else {},
        closed=closed_at is not None,
        closed_at=closed_at,
        updated_at=(now - updated).isoformat(),
        milestone=milestone,
        parent=parent,
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
        team=TEAM,
        filters=[
            *STARTER_FILTERS,
            SavedFilter("Tidepool bugs", "repo:octo-dev/tidepool label:bug"),
        ],
    )


def github() -> FakeGitHub:
    tide, lantern = REPOS
    v04, v10 = "v0.4", "v1.0"  # both repos have a v1.0; they are different milestones
    issues = [
        _issue(
            tide,
            12,
            "Sync stalls when the tide table is empty",
            VIEWER,
            updated=timedelta(minutes=5),
            labels=("bug", "in-progress"),
            milestone=v04,
        ),
        _issue(
            tide,
            15,
            "Add a weekly digest of high tides",
            VIEWER,
            updated=timedelta(minutes=40),
            labels=("enhancement", "todo", "in-review"),  # two status labels
            milestone=v04,
        ),
        _issue(
            tide,
            18,
            "Document the import format",
            updated=timedelta(hours=3),
            labels=("documentation",),
            milestone=v10,
        ),
        _issue(
            lantern,
            4,
            "Lantern glow ignores the dark theme",
            VIEWER,
            "sam-reef",
            updated=timedelta(hours=7),
            project_status="In Progress",
            milestone=v10,
        ),
        _issue(
            lantern,
            7,
            "Cache fish sightings between runs",
            "sam-reef",
            updated=timedelta(days=1),
            labels=("ready-for-human",),
            project_status="Todo",
            milestone=v10,
            parent=COUNTING,
        ),
        # Not on the project.
        _issue(
            lantern,
            9,
            "Count lanterns per reef",
            VIEWER,
            updated=timedelta(days=2),
            labels=("ready-for-human",),
            milestone=v10,
        ),
        _issue(
            lantern,
            11,
            "Wait for the depth sensor API",
            VIEWER,
            updated=timedelta(days=3),
            project_status="Blocked",
            milestone=v10,
            parent=COUNTING,
        ),
        _issue(
            lantern,
            13,
            "Pick a palette for night dives",
            updated=timedelta(days=6),
            labels=("needs-triage",),
            project_status="Todo",
        ),
        _issue(
            tide,
            10,
            "Tide chart renders upside down",
            VIEWER,
            updated=timedelta(days=2),
            labels=("bug",),
            closed_days_ago=2,
            milestone=v04,
        ),
        _issue(
            lantern,
            2,
            "Import the old lantern log",
            VIEWER,
            updated=timedelta(days=40),
            closed_days_ago=40,
            milestone=v10,
        ),
    ]
    board = Project(PROJECT, "Lanternfish board", ("Todo", "In Progress", "Blocked", "Done"))
    return FakeGitHub(
        viewer=VIEWER,
        issues=issues,
        details=_details(issues),
        labels={
            tide: ["bug", "documentation", "enhancement", "in-progress", "in-review", "todo"],
            lantern: ["bug", "needs-triage", "ready-for-human"],
        },
        projects={lantern: [board]},
        milestones={tide: [v04, v10], lantern: [v10]},
        assignable={tide: [VIEWER, *TEAM], lantern: [VIEWER, "sam-reef", "ray-coral"]},
    )


def _at(day: int, hour: int) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=UTC)


def _details(issues: list[Issue]) -> dict[str, IssueDetail]:
    """Bodies, comments, activity and hierarchy for a few issues; the rest have none."""
    tide12, tide15, _, lantern4, lantern7, lantern9, lantern11 = issues[:7]
    theme = "Night mode"
    sync_bug = IssueDetail(
        tide12,
        body=(
            "Syncing hangs forever when the station returns an empty tide table.\n\n"
            "**Steps**\n\n"
            "1. Point `tidepool` at a station with no readings\n"
            "2. Run `tidepool sync`\n\n"
            "```text\nsyncing station 9414290... (no progress)\n```\n\n"
            "Expected: a warning and an empty table."
        ),
        activity=(
            Event("sam-reef", _at(1, 9), "labeled", "bug"),
            Event("sam-reef", _at(1, 9), "milestoned", "v0.4"),
            Event("sam-reef", _at(1, 10), "commented", "Reproduced on station 9414290."),
            Event(VIEWER, _at(2, 14), "assigned", VIEWER),
            Event(VIEWER, _at(2, 14), "labeled", "in-progress"),
            Event(
                VIEWER,
                _at(3, 11),
                "commented",
                "The pager loops on an empty `next` cursor. Fix:\n\n"
                "```python\nif not page.next:\n    break\n```",
            ),
        ),
    )
    digest = IssueDetail(
        tide15,
        body="A Monday email listing the week's three highest tides per station.",
        activity=(
            Event(VIEWER, _at(4, 8), "labeled", "todo"),
            Event(VIEWER, _at(5, 16), "closed", "not planned"),
            Event("sam-reef", _at(6, 9), "reopened"),
            Event("sam-reef", _at(6, 9), "commented", "Users asked for this again; reopening."),
            Event(VIEWER, _at(7, 10), "labeled", "in-review"),
        ),
    )
    glow = IssueDetail(
        lantern4,
        body="The glow stays bright yellow in the dark theme. It should dim to amber.",
        project_fields=(
            ProjectField("Lanternfish board", "Status", "In Progress"),
            ProjectField("Lanternfish board", "Theme", theme),
            ProjectField("Lanternfish board", "Iteration", "Sprint 7"),
        ),
        activity=(
            Event(VIEWER, _at(8, 9), "assigned", "sam-reef"),
            Event(VIEWER, _at(8, 9), "assigned", VIEWER),
            Event("sam-reef", _at(9, 15), "commented", "Screenshot attached on the board."),
        ),
    )
    counting = IssueDetail(
        lantern9,
        body="Track how many lanterns each reef has.\n\n- [x] Schema\n- [ ] Sightings import",
        sub_issues=(lantern7, lantern11),
    )
    sightings = IssueDetail(lantern7, parent=lantern9)
    sensor = IssueDetail(
        lantern11,
        body="Blocked until the depth sensor API ships its `v2` endpoint.",
        parent=lantern9,
        project_fields=(
            ProjectField("Lanternfish board", "Status", "Blocked"),
            ProjectField("Lanternfish board", "Theme", theme),
        ),
    )
    return {
        detail.issue.key: detail for detail in (sync_bug, digest, glow, counting, sightings, sensor)
    }
