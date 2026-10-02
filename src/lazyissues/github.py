"""The GitHub gateway: the only code that talks to GitHub (ADR 0002)."""

import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

import httpx

from lazyissues.models import Event, EventKind, Issue, IssueDetail, ProjectField

API_URL = "https://api.github.com/graphql"
SEARCH_LIMIT = 1000  # GitHub search never returns more than this


class GitHubError(Exception):
    pass


class Gateway(Protocol):
    async def search_issues(self, query: str) -> list[Issue]: ...

    async def issue_detail(self, repo: str, number: int) -> IssueDetail: ...


def gh_token() -> str:
    """The token of the logged-in `gh` CLI user."""
    if shutil.which("gh") is None:
        raise GitHubError("The GitHub CLI (`gh`) isn't installed: https://cli.github.com/")
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise GitHubError("`gh` isn't logged in. Run `gh auth login`.")
    return result.stdout.strip()


# Every query that builds an `Issue` selects these fields, so `_issue` can read them.
_ISSUE_FIELDS = """
fragment IssueFields on Issue {
  number title url state
  repository { nameWithOwner }
  assignees(first: 10) { nodes { login } }
  labels(first: 20) { nodes { name } }
  projectItems(first: 10) {
    nodes {
      project { number owner { ... on Actor { login } } }
      fieldValueByName(name: "Status") {
        ... on ProjectV2ItemFieldSingleSelectValue { name }
      }
    }
  }
}
"""

_SEARCH = (
    """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ...IssueFields }
  }
}
"""
    + _ISSUE_FIELDS
)

# `last: 100` keeps the newest activity of a long issue, still oldest first. The project
# items here merge with those in `IssueFields`, adding every field's value.
_DETAIL = (
    """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      ...IssueFields
      body
      milestone { title }
      parent { ...IssueFields }
      subIssues(first: 50) { nodes { ...IssueFields } }
      projectItems(first: 10) {
        nodes {
          project { title }
          fieldValues(first: 30) {
            nodes {
              ... on ProjectV2ItemFieldSingleSelectValue { name field { ...FieldName } }
              ... on ProjectV2ItemFieldIterationValue { title field { ...FieldName } }
            }
          }
        }
      }
      timelineItems(last: 100, itemTypes: [
        ISSUE_COMMENT, LABELED_EVENT, UNLABELED_EVENT, ASSIGNED_EVENT, UNASSIGNED_EVENT,
        MILESTONED_EVENT, DEMILESTONED_EVENT, CLOSED_EVENT, REOPENED_EVENT
      ]) {
        nodes {
          __typename
          ... on IssueComment { actor: author { login } createdAt body }
          ... on LabeledEvent { actor { login } createdAt label { name } }
          ... on UnlabeledEvent { actor { login } createdAt label { name } }
          ... on AssignedEvent { actor { login } createdAt assignee { ...Login } }
          ... on UnassignedEvent { actor { login } createdAt assignee { ...Login } }
          ... on MilestonedEvent { actor { login } createdAt milestoneTitle }
          ... on DemilestonedEvent { actor { login } createdAt milestoneTitle }
          ... on ClosedEvent { actor { login } createdAt stateReason }
          ... on ReopenedEvent { actor { login } createdAt }
        }
      }
    }
  }
}

fragment FieldName on ProjectV2FieldConfiguration { ... on ProjectV2FieldCommon { name } }
fragment Login on Assignee { ... on Actor { login } }
"""
    + _ISSUE_FIELDS
)

# The activity kind of each timeline item type, and where its text lives.
_EVENTS: dict[str, tuple[EventKind, Callable[[dict[str, Any]], str | None]]] = {
    "IssueComment": ("commented", lambda node: node["body"]),
    "LabeledEvent": ("labeled", lambda node: node["label"]["name"]),
    "UnlabeledEvent": ("unlabeled", lambda node: node["label"]["name"]),
    "AssignedEvent": ("assigned", lambda node: _login(node["assignee"])),
    "UnassignedEvent": ("unassigned", lambda node: _login(node["assignee"])),
    "MilestonedEvent": ("milestoned", lambda node: node["milestoneTitle"]),
    "DemilestonedEvent": ("demilestoned", lambda node: node["milestoneTitle"]),
    "ClosedEvent": (
        "closed",
        lambda node: (node["stateReason"] or "COMPLETED").lower().replace("_", " "),
    ),
    "ReopenedEvent": ("reopened", lambda node: ""),
}


def _login(actor: dict[str, Any] | None) -> str:
    """GitHub shows a deleted account as `ghost`."""
    return actor["login"] if actor else "ghost"


def _event(node: dict[str, Any]) -> Event:
    kind, text = _EVENTS[node["__typename"]]
    return Event(
        actor=_login(node["actor"]),
        at=datetime.fromisoformat(node["createdAt"]),
        kind=kind,
        text=text(node) or "",
    )


def _project_fields(item: dict[str, Any]) -> list[ProjectField]:
    """Single-select and iteration values; other field types come back as `{}`."""
    return [
        ProjectField(
            item["project"]["title"],
            value["field"]["name"],
            value.get("name") or value.get("title") or "",
        )
        for value in item["fieldValues"]["nodes"]
        if value
    ]


def _detail(node: dict[str, Any]) -> IssueDetail:
    return IssueDetail(
        issue=_issue(node),
        milestone=node["milestone"] and node["milestone"]["title"],
        body=node["body"],
        parent=node["parent"] and _issue(node["parent"]),
        sub_issues=tuple(_issue(sub) for sub in node["subIssues"]["nodes"]),
        project_fields=tuple(
            field for item in node["projectItems"]["nodes"] for field in _project_fields(item)
        ),
        activity=tuple(_event(item) for item in node["timelineItems"]["nodes"]),
    )


def _issue(node: dict[str, Any]) -> Issue:
    return Issue(
        repo=node["repository"]["nameWithOwner"],
        number=node["number"],
        title=node["title"],
        url=node["url"],
        assignees=tuple(a["login"] for a in node["assignees"]["nodes"]),
        labels=tuple(label["name"] for label in node["labels"]["nodes"]),
        closed=node["state"] == "CLOSED",
        project_statuses=_project_statuses(node["projectItems"]["nodes"]),
    )


def _project_statuses(items: list[dict[str, Any]]) -> dict[str, str]:
    """Each project's Status option, keyed "owner/number"; unset ones are left out."""
    statuses = {}
    for item in items:
        if status := (item["fieldValueByName"] or {}).get("name"):
            project = item["project"]
            statuses[f"{project['owner']['login']}/{project['number']}"] = status
    return statuses


class GraphQLGateway:
    def __init__(self, token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if transport is None and "pytest" in sys.modules:
            raise RuntimeError("Tests must not reach GitHub; pass a fake transport.")
        self._client = httpx.AsyncClient(
            transport=transport,
            headers={"Authorization": f"bearer {token}"},
            timeout=30,
        )

    async def _query(self, query: str, **variables: Any) -> dict[str, Any]:
        try:
            response = await self._client.post(
                API_URL, json={"query": query, "variables": variables}
            )
        except httpx.HTTPError as e:
            raise GitHubError(f"Couldn't reach GitHub: {e}") from e
        if response.status_code == 401:
            raise GitHubError("GitHub rejected the `gh` token. Run `gh auth login`.")
        if response.is_error:
            raise GitHubError(f"GitHub returned HTTP {response.status_code}.")
        body = response.json()
        if errors := body.get("errors"):
            raise GitHubError("; ".join(e["message"] for e in errors))
        return body["data"]

    async def search_issues(self, query: str) -> list[Issue]:
        issues: list[Issue] = []
        after = None
        while len(issues) < SEARCH_LIMIT:
            page = (await self._query(_SEARCH, q=query, after=after))["search"]
            issues += [_issue(node) for node in page["nodes"] if node]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        return issues

    async def issue_detail(self, repo: str, number: int) -> IssueDetail:
        owner, name = repo.split("/", 1)
        data = await self._query(_DETAIL, owner=owner, name=name, number=number)
        return _detail(data["repository"]["issue"])
