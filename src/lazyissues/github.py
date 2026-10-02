"""The GitHub gateway: the only code that talks to GitHub (ADR 0002)."""

import shutil
import subprocess
import sys
from typing import Any, Protocol

import httpx

from lazyissues.models import Issue

API_URL = "https://api.github.com/graphql"
SEARCH_LIMIT = 1000  # GitHub search never returns more than this


class GitHubError(Exception):
    pass


class Gateway(Protocol):
    async def search_issues(self, query: str) -> list[Issue]: ...


def gh_token() -> str:
    """The token of the logged-in `gh` CLI user."""
    if shutil.which("gh") is None:
        raise GitHubError("The GitHub CLI (`gh`) isn't installed: https://cli.github.com/")
    result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise GitHubError("`gh` isn't logged in. Run `gh auth login`.")
    return result.stdout.strip()


_SEARCH = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
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
    }
  }
}
"""


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
