import pytest

from lazyissues import demo
from lazyissues.config import Config, Repo, SavedFilter, Status
from lazyissues.discovery import Proposal, RepoOffer, discover, find_repo, first_run
from lazyissues.fake import FakeGitHub
from lazyissues.github import GitHubError
from lazyissues.models import Issue, Project

STARTER_FILTERS = [
    SavedFilter("Ready for me", "label:ready-for-human"),
    SavedFilter("Needs triage", "label:needs-triage"),
]


def issue(repo: str, number: int = 1) -> Issue:
    return Issue(repo, number, "An issue", f"https://github.com/{repo}/issues/{number}", ("me",))


BOARD = Project("o/1", "Board", ("Todo", "In Progress", "In Review", "Done", "Closed", "Complete"))


async def test_proposes_a_config_for_the_demo_data():
    proposal = await discover(demo.github())

    assert proposal.config() == Config(
        repos=[Repo("octo-dev/lanternfish", "project", "octo-dev/3"), Repo("octo-dev/tidepool")],
        statuses=[Status("Todo"), Status("In Progress", active=True), Status("Blocked")],
        team=["octo-dev"],
        filters=STARTER_FILTERS,
    )
    assert not proposal.missing_project_scope


async def test_a_repo_linked_to_one_project_uses_its_status_options_except_done_ones():
    github = FakeGitHub(viewer="me", issues=[issue("o/r")], projects={"o/r": [BOARD]})
    config = (await discover(github)).config()
    assert config.repos == [Repo("o/r", "project", "o/1")]
    assert [status.name for status in config.statuses] == ["Todo", "In Progress", "In Review"]


async def test_a_repo_with_no_or_several_projects_uses_labels_and_no_label_is_a_status_yet():
    other = Project("o/2", "Other", ("Doing",))
    github = FakeGitHub(
        viewer="me",
        issues=[issue("o/a"), issue("o/b"), issue("o/a", 2)],
        labels={"o/a": ["bug", "todo"], "o/b": ["bug"]},
        projects={"o/b": [BOARD, other]},
    )
    config = (await discover(github)).config()
    assert config.repos == [Repo("o/a"), Repo("o/b")]
    assert config.statuses == []


async def test_without_the_project_scope_every_repo_uses_labels():
    github = FakeGitHub(
        viewer="me", scopes={"repo"}, issues=[issue("o/r")], projects={"o/r": [BOARD]}
    )
    proposal = await discover(github)
    assert proposal.missing_project_scope
    assert proposal.config().repos == [Repo("o/r")]


async def test_status_labels_and_project_options_with_one_name_are_one_status():
    github = FakeGitHub(
        viewer="me",
        issues=[issue("o/board"), issue("o/labels")],
        labels={"o/labels": ["bug", "in-progress", "blocked"]},
        projects={"o/board": [BOARD]},
    )
    proposal = await discover(github)
    proposal.pick_labels("o/labels", {"blocked", "in-progress"})
    assert proposal.statuses == [
        Status("Todo"),
        Status("In Progress", active=True),
        Status("In Review"),
        Status("blocked"),
    ]


async def test_reordering_and_marking_active_survive_source_changes():
    github = FakeGitHub(
        viewer="me",
        issues=[issue("o/r")],
        labels={"o/r": ["todo", "doing"]},
        projects={"o/r": [BOARD]},
    )
    proposal = await discover(github)
    proposal.set_statuses([Status("In Review"), Status("Todo", active=True), Status("In Progress")])
    assert proposal.statuses == [
        Status("In Review"),
        Status("Todo", active=True),
        Status("In Progress"),
    ]

    proposal.set_source(Repo("o/r"))
    proposal.pick_labels("o/r", {"todo", "doing"})
    assert proposal.statuses == [Status("todo", active=True), Status("doing")]
    assert proposal.config().repos == [Repo("o/r")]


async def test_an_added_org_repo_joins_the_proposal_and_removed_repos_leave_it():
    github = FakeGitHub(viewer="me", issues=[issue("me/mine")], projects={"acme/web": [BOARD]})
    proposal = await discover(github)
    proposal.add("acme/web", await find_repo(github, "acme/web", read_projects=True))
    proposal.choose_repos({"acme/web"})
    assert proposal.config().repos == [Repo("acme/web", "project", "o/1")]


async def test_a_repo_added_with_other_capitals_is_the_same_repo():
    github = demo.github()
    proposal = await discover(github)
    offer = await find_repo(github, "Octo-Dev/Tidepool", read_projects=True)
    assert proposal.add("Octo-Dev/Tidepool", offer) == "octo-dev/tidepool"
    assert proposal.repos == ["octo-dev/lanternfish", "octo-dev/tidepool"]


async def test_a_re_added_repo_whose_project_is_gone_falls_back_to_labels():
    github = FakeGitHub(viewer="me", issues=[issue("o/r")], projects={"o/r": [BOARD]})
    proposal = await discover(github)
    proposal.add("o/r", RepoOffer(labels=(), projects=()))
    assert proposal.config().repos == [Repo("o/r")]
    assert proposal.statuses == []


def test_an_empty_status_list_is_a_valid_proposal():
    proposal = Proposal(first_run("me"))
    proposal.add("o/r", RepoOffer(labels=("bug",), projects=()))
    assert proposal.config() == Config([Repo("o/r")], [], ["me"], STARTER_FILTERS)


class FlakyGitHub(FakeGitHub):
    """A fake whose project reads fail for one repo, as under SAML enforcement."""

    async def repo_projects(self, repo: str) -> list[Project]:
        if repo == "o/locked":
            raise GitHubError("Resource protected by organization SAML enforcement.")
        return await super().repo_projects(repo)


async def test_a_repo_github_wont_read_is_left_out_with_the_reason():
    github = FlakyGitHub(viewer="me", issues=[issue("o/locked"), issue("o/open")])
    proposal = await discover(github)
    assert proposal.repos == ["o/open"]
    assert "SAML" in proposal.unreadable["o/locked"]


def test_a_config_needs_a_repo():
    with pytest.raises(ValueError, match="at least one repo"):
        Proposal(first_run("me")).config()


async def test_a_rerun_keeps_the_config_and_adds_statuses_its_sources_now_offer():
    current = demo.config()
    proposal = await discover(demo.github(), current)
    assert proposal.config() == Config(
        repos=current.repos,
        # Lanternfish's board offers Blocked, which the config doesn't list yet.
        statuses=[*current.statuses, Status("Blocked")],
        team=current.team,
        filters=current.filters,
    )


def test_a_kept_repo_added_again_once_readable_is_listed_once():
    proposal = Proposal(Config([Repo("o/r")], []), unreadable={"o/r": "SAML"})
    proposal.add("O/R", RepoOffer(labels=(), projects=()))
    assert proposal.config().repos == [Repo("O/R")]
