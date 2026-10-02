"""The GitHub gateway: the only code that talks to GitHub (ADR 0002)."""

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Protocol

import httpx

from lazyissues.models import (
    CloseReason,
    Event,
    EventKind,
    Issue,
    IssueChanges,
    IssueDetail,
    Milestone,
    Project,
    ProjectField,
    parse_key,
)
from lazyissues.statuses import normalize

API_URL = "https://api.github.com/graphql"
SEARCH_LIMIT = 1000  # GitHub search never returns more than this
LABEL_COLOR = "ededed"  # GitHub's gray, for a status label lazyissues creates


class GitHubError(Exception):
    pass


class Gateway(Protocol):
    async def search_issues(self, query: str) -> list[Issue]: ...

    async def issue_detail(self, repo: str, number: int) -> IssueDetail: ...

    async def whoami(self) -> tuple[str, set[str]]: ...

    async def repo_labels(self, repo: str) -> list[str]: ...

    async def repo_projects(self, repo: str) -> list[Project]: ...

    async def repo_milestones(self, repo: str) -> list[Milestone]: ...

    async def assignable_users(self, repo: str) -> list[str]: ...

    async def project_status_options(self, project: str) -> list[str]:
        """The Status field's options of project "owner/number", in board order."""
        ...

    # Writes. Each issue is `repo` ("owner/name") and `number`.

    async def add_label(self, repo: str, number: int, name: str) -> None:
        """Add the repo's label matching `name` as statuses match, creating it if none does."""
        ...

    async def remove_labels(self, repo: str, number: int, names: Sequence[str]) -> None: ...

    async def add_to_project(self, repo: str, number: int, project: str) -> None: ...

    async def set_project_status(self, repo: str, number: int, project: str, status: str) -> None:
        """Set the issue's Status on `project` to the option matching `status`."""
        ...

    async def close_issue(
        self, repo: str, number: int, reason: CloseReason, duplicate_of: str | None = None
    ) -> None:
        """Close with `reason`; a duplicate names its original as "owner/repo#number"."""
        ...

    async def reopen_issue(self, repo: str, number: int) -> None: ...

    async def assign(self, repo: str, number: int, login: str) -> None: ...

    # These writes return the issue's detail as GitHub has it once the write is done.

    async def comment(self, repo: str, number: int, body: str) -> IssueDetail: ...

    async def change_assignees(
        self, repo: str, number: int, add: Sequence[str], remove: Sequence[str]
    ) -> IssueDetail:
        """Add and remove assignees by login, leaving any others as they are on GitHub."""
        ...

    async def create_issue(
        self,
        repo: str,
        title: str,
        *,
        body: str = "",
        labels: Sequence[str] = (),
        milestone: str | None = None,
    ) -> IssueDetail: ...

    async def update_issue(self, repo: str, number: int, changes: IssueChanges) -> IssueDetail:
        """Change only the fields in `changes`, so edits made meanwhile to others stay."""
        ...


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
  number title url state closedAt
  repository { nameWithOwner }
  assignees(first: 10) { nodes { login } }
  labels(first: 20) { nodes { name } }
  milestone { title }
  parent { number repository { nameWithOwner } }
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

# Every query or mutation that builds an `IssueDetail` selects these fields, so `_detail`
# can read them; writes return them so the app shows GitHub's copy after each change.
# `last: 100` keeps the newest activity of a long issue, still oldest first. The project
# items here merge with those in `IssueFields`, adding every field's value.
_DETAIL_FIELDS = (
    """
fragment DetailFields on Issue {
  ...IssueFields
  body
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

fragment FieldName on ProjectV2FieldConfiguration { ... on ProjectV2FieldCommon { name } }
fragment Login on Assignee { ... on Actor { login } }
"""
    + _ISSUE_FIELDS
)

_DETAIL = (
    """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) { issue(number: $number) { ...DetailFields } }
}
"""
    + _DETAIL_FIELDS
)

_REPO_ID = """
query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) { id } }
"""

# What every write needs: the IDs of the issue and its repo, and the issue's labels and
# project items.
_WRITE_TARGET = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    id
    issue(number: $number) {
      id
      labels(first: 100) { nodes { id name } }
      projectItems(first: 100) { nodes { id project { id } } }
    }
  }
}
"""

# A user or organization's project, with its Status field's options in board order.
_PROJECT = """
query($login: String!, $number: Int!) {
  repositoryOwner(login: $login) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id
        field(name: "Status") { ... on ProjectV2SingleSelectField { id options { id name } } }
      }
    }
  }
}
"""

_USER = "query($login: String!) { user(login: $login) { id } }"

_ASSIGNABLE_USERS = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    assignableUsers(first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes { login }
    }
  }
}
"""

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


_VIEWER = "query { viewer { login } }"

_LABELS = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    labels(first: 100, after: $after, orderBy: {field: NAME, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { id name }
    }
  }
}
"""

_PROJECTS = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    projectsV2(first: 20) {
      nodes {
        number title closed
        owner { ... on Actor { login } }
        field(name: "Status") { ... on ProjectV2SingleSelectField { options { name } } }
      }
    }
  }
}
"""

_MILESTONES = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    milestones(
      first: 100, after: $after, states: [OPEN], orderBy: {field: DUE_DATE, direction: ASC}
    ) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id title
        open: issues(states: OPEN) { totalCount }
        closed: issues(states: CLOSED) { totalCount }
      }
    }
  }
}
"""


def _known(ids: dict[str, str], name: str, missing: str) -> str:
    if name not in ids:
        raise GitHubError(missing)
    return ids[name]


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
        closed_at=node["closedAt"],
        milestone=node["milestone"] and node["milestone"]["title"],
        parent=_parent_key(node),
        project_statuses=_project_statuses(node["projectItems"]["nodes"]),
    )


def _parent_key(node: dict[str, Any]) -> str | None:
    parent = node["parent"]
    return parent and f"{parent['repository']['nameWithOwner']}#{parent['number']}"


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
        return (await self._send(query, **variables))[0]

    async def _send(self, query: str, **variables: Any) -> tuple[dict[str, Any], httpx.Headers]:
        """The query's data, and the response headers."""
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
        return body["data"], response.headers

    async def _nodes(
        self, query: str, connection: Callable[[dict[str, Any]], Any], **variables: Any
    ) -> list[dict[str, Any]]:
        """Every node of a paged connection, which `connection` picks out of the data."""
        nodes: list[dict[str, Any]] = []
        after = None
        while len(nodes) < SEARCH_LIMIT:
            page = connection(await self._query(query, after=after, **variables))
            nodes += [node for node in page["nodes"] if node]
            if not page["pageInfo"]["hasNextPage"]:
                break
            after = page["pageInfo"]["endCursor"]
        return nodes

    async def search_issues(self, query: str) -> list[Issue]:
        return [_issue(node) for node in await self._nodes(_SEARCH, lambda d: d["search"], q=query)]

    async def issue_detail(self, repo: str, number: int) -> IssueDetail:
        owner, name = repo.split("/", 1)
        data = await self._query(_DETAIL, owner=owner, name=name, number=number)
        return _detail(data["repository"]["issue"])

    async def whoami(self) -> tuple[str, set[str]]:
        """The viewer's login, and the `gh` token's OAuth scopes from the response header."""
        data, headers = await self._send(_VIEWER)
        scopes = {scope.strip() for scope in headers.get("X-OAuth-Scopes", "").split(",")}
        return data["viewer"]["login"], scopes - {""}

    async def repo_labels(self, repo: str) -> list[str]:
        return [label["name"] for label in await self._labels(repo)]

    async def repo_projects(self, repo: str) -> list[Project]:
        """The open projects linked to `repo`."""
        owner, name = repo.split("/")
        data = await self._query(_PROJECTS, owner=owner, name=name)
        return [
            Project(
                f"{_login(node['owner'])}/{node['number']}",
                node["title"],
                tuple(option["name"] for option in (node["field"] or {}).get("options", [])),
            )
            for node in data["repository"]["projectsV2"]["nodes"]
            if node and not node["closed"]
        ]

    async def repo_milestones(self, repo: str) -> list[Milestone]:
        """`repo`'s open milestones, soonest due first, with their issue counts."""
        return [
            Milestone(repo, node["title"], node["open"]["totalCount"], node["closed"]["totalCount"])
            for node in await self._milestones(repo)
        ]

    async def project_status_options(self, project: str) -> list[str]:
        return [option["name"] for option in (await self._project(project))["field"]["options"]]

    async def add_label(self, repo: str, number: int, name: str) -> None:
        target = await self._write_target(repo, number)
        label = _named(await self._labels(repo), name)
        if label is None:
            label_input = {"repositoryId": target["id"], "name": name, "color": LABEL_COLOR}
            created = await self._mutate("createLabel", label_input, "label { id }")
            label = created["label"]
        await self._mutate(
            "addLabelsToLabelable",
            {"labelableId": target["issue"]["id"], "labelIds": [label["id"]]},
        )

    async def remove_labels(self, repo: str, number: int, names: Sequence[str]) -> None:
        issue = (await self._write_target(repo, number))["issue"]
        ids = [label["id"] for label in issue["labels"]["nodes"] if label["name"] in names]
        if ids:
            await self._mutate(
                "removeLabelsFromLabelable", {"labelableId": issue["id"], "labelIds": ids}
            )

    async def add_to_project(self, repo: str, number: int, project: str) -> None:
        issue = (await self._write_target(repo, number))["issue"]
        project_id = (await self._project(project))["id"]
        await self._mutate(
            "addProjectV2ItemById", {"projectId": project_id, "contentId": issue["id"]}
        )

    async def set_project_status(self, repo: str, number: int, project: str, status: str) -> None:
        issue = (await self._write_target(repo, number))["issue"]
        board = await self._project(project)
        items = issue["projectItems"]["nodes"]
        item = next((item for item in items if item["project"]["id"] == board["id"]), None)
        if item is None:
            raise GitHubError(f"{repo}#{number} isn't on project {project}.")
        option = _named(board["field"]["options"], status)
        if option is None:
            raise GitHubError(f"Project {project} has no Status {status}.")
        await self._mutate(
            "updateProjectV2ItemFieldValue",
            {
                "projectId": board["id"],
                "itemId": item["id"],
                "fieldId": board["field"]["id"],
                "value": {"singleSelectOptionId": option["id"]},
            },
        )

    async def close_issue(
        self, repo: str, number: int, reason: CloseReason, duplicate_of: str | None = None
    ) -> None:
        close = {"issueId": await self._issue_id(repo, number), "stateReason": reason.value}
        if duplicate_of is not None:
            close["duplicateIssueId"] = await self._issue_id(*parse_key(duplicate_of))
        await self._mutate("closeIssue", close)

    async def reopen_issue(self, repo: str, number: int) -> None:
        await self._mutate("reopenIssue", {"issueId": await self._issue_id(repo, number)})

    async def assignable_users(self, repo: str) -> list[str]:
        owner, name = repo.split("/", 1)
        users = await self._nodes(
            _ASSIGNABLE_USERS,
            lambda d: d["repository"]["assignableUsers"],
            owner=owner,
            name=name,
        )
        return [user["login"] for user in users]

    async def comment(self, repo: str, number: int, body: str) -> IssueDetail:
        fields = {"subjectId": await self._issue_id(repo, number), "body": body}
        return await self._write("addComment", fields, "subject")

    async def assign(self, repo: str, number: int, login: str) -> None:
        issue_id = await self._issue_id(repo, number)
        await self._mutate(
            "addAssigneesToAssignable",
            {"assignableId": issue_id, "assigneeIds": [await self._user_id(login)]},
        )

    async def change_assignees(
        self, repo: str, number: int, add: Sequence[str], remove: Sequence[str]
    ) -> IssueDetail:
        issue_id, *user_ids = await asyncio.gather(
            self._issue_id(repo, number), *(self._user_id(login) for login in [*add, *remove])
        )
        steps = [
            (mutation, ids)
            for mutation, ids in (
                ("addAssigneesToAssignable", user_ids[: len(add)]),
                ("removeAssigneesFromAssignable", user_ids[len(add) :]),
            )
            if ids
        ]
        detail = None
        for mutation, ids in steps:
            try:
                detail = await self._write(
                    mutation, {"assignableId": issue_id, "assigneeIds": ids}, "assignable"
                )
            except GitHubError as e:
                if detail is not None:  # the add went through; say so, or it looks undone
                    added, missed = ", ".join(add), ", ".join(remove)
                    raise GitHubError(f"Added {added}, but couldn't remove {missed}: {e}") from e
                raise
        return detail or await self.issue_detail(repo, number)

    async def _user_id(self, login: str) -> str:
        user = (await self._query(_USER, login=login))["user"]
        if user is None:
            raise GitHubError(f"No GitHub user {login}.")
        return user["id"]

    async def create_issue(
        self,
        repo: str,
        title: str,
        *,
        body: str = "",
        labels: Sequence[str] = (),
        milestone: str | None = None,
    ) -> IssueDetail:
        owner, name = repo.split("/", 1)
        repository = (await self._query(_REPO_ID, owner=owner, name=name))["repository"]
        fields = {
            "repositoryId": repository["id"],
            "title": title,
            "body": body,
            "labelIds": await self._label_ids(repo, labels),
            "milestoneId": await self._milestone_id(repo, milestone),
        }
        return await self._write("createIssue", fields, "issue")

    async def update_issue(self, repo: str, number: int, changes: IssueChanges) -> IssueDetail:
        fields: dict[str, Any] = {"id": await self._issue_id(repo, number)}
        if "title" in changes:
            fields["title"] = changes["title"]
        if "body" in changes:
            fields["body"] = changes["body"]
        if "milestone" in changes:  # None removes it
            fields["milestoneId"] = await self._milestone_id(repo, changes["milestone"])
        return await self._write("updateIssue", fields, "issue")

    async def _mutate(
        self,
        mutation: str,
        fields: dict[str, Any],
        returning: str = "clientMutationId",
        fragments: str = "",
    ) -> dict[str, Any]:
        """Run `mutation` with `fields` as its input, selecting `returning`."""
        kind = mutation[0].upper() + mutation[1:]
        query = f"mutation($input: {kind}Input!) {{ {mutation}(input: $input) {{ {returning} }} }}"
        return (await self._query(query + fragments, input=fields))[mutation]

    async def _write(self, mutation: str, fields: dict[str, Any], issue: str) -> IssueDetail:
        """Run `mutation`, and build the issue's detail from its payload's `issue` field."""
        returning = f"{issue} {{ ...DetailFields }}"
        return _detail((await self._mutate(mutation, fields, returning, _DETAIL_FIELDS))[issue])

    async def _labels(self, repo: str) -> list[dict[str, Any]]:
        """Every label of `repo`, with its ID."""
        owner, name = repo.split("/")
        return await self._nodes(
            _LABELS, lambda d: d["repository"]["labels"], owner=owner, name=name
        )

    async def _milestones(self, repo: str) -> list[dict[str, Any]]:
        """Every open milestone of `repo`, soonest due first, with its ID."""
        owner, name = repo.split("/", 1)
        return await self._nodes(
            _MILESTONES, lambda d: d["repository"]["milestones"], owner=owner, name=name
        )

    async def _label_ids(self, repo: str, labels: Sequence[str]) -> list[str]:
        if not labels:
            return []
        ids = {label["name"]: label["id"] for label in await self._labels(repo)}
        return [_known(ids, label, f"{repo} has no label {label!r}.") for label in labels]

    async def _milestone_id(self, repo: str, milestone: str | None) -> str | None:
        if milestone is None:
            return None
        ids = {m["title"]: m["id"] for m in await self._milestones(repo)}
        return _known(ids, milestone, f"{repo} has no open milestone {milestone!r}.")

    async def _write_target(self, repo: str, number: int) -> dict[str, Any]:
        owner, name = repo.split("/", 1)
        return (await self._query(_WRITE_TARGET, owner=owner, name=name, number=number))[
            "repository"
        ]

    async def _issue_id(self, repo: str, number: int) -> str:
        return (await self._write_target(repo, number))["issue"]["id"]

    async def _project(self, project: str) -> dict[str, Any]:
        """The project "owner/number" with its Status field."""
        login, number = project.split("/", 1)
        owner = (await self._query(_PROJECT, login=login, number=int(number)))["repositoryOwner"]
        board = (owner or {}).get("projectV2")
        if board is None:
            raise GitHubError(f"Can't find project {project}.")
        if not board["field"]:
            raise GitHubError(f"Project {project} has no single-select Status field.")
        return board


def _named(nodes: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The node whose `name` matches `name` the way statuses match."""
    return next((node for node in nodes if normalize(node["name"]) == normalize(name)), None)
