"""Building the GitHub issue searches a view sends: scoped to the repo set, with states."""

from datetime import date

_SCOPE_QUALIFIERS = ("repo:", "org:", "user:")
_STATE_QUALIFIERS = ("is:open", "is:closed", "state:", "-is:open", "-is:closed", "-state:")
_CLOSED_QUALIFIERS = ("is:closed", "state:closed", "-is:open", "-state:open")


def scoped(query: str, repos: list[str]) -> str:
    """Limit `query` to the repo set unless it already names its own scope."""
    if _names(query, _SCOPE_QUALIFIERS):
        return query
    return " ".join([query, *(f"repo:{repo}" for repo in repos)])


def with_states(query: str, closed_since: date | None) -> list[str]:
    """The searches that read `query`'s open issues, and its issues closed on or after
    `closed_since` when given. A query that names its own state runs as written."""
    if _names(query, _STATE_QUALIFIERS):
        return [query]
    searches = [f"{query} is:open"]
    if closed_since is not None:
        searches.append(f"{query} is:closed closed:>={closed_since.isoformat()}")
    return searches


def asks_for_closed(query: str) -> bool:
    """Whether `query` names a state that reads closed issues."""
    return _names(query, _CLOSED_QUALIFIERS)


def _names(query: str, qualifiers: tuple[str, ...]) -> bool:
    return any(term.startswith(qualifiers) for term in query.split())
