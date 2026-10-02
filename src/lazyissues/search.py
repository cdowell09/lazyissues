"""Scoping GitHub issue search queries to the repo set."""

_SCOPE_QUALIFIERS = ("repo:", "org:", "user:")


def scoped(query: str, repos: list[str]) -> str:
    """Limit `query` to the repo set unless it already names its own scope."""
    if any(term.startswith(_SCOPE_QUALIFIERS) for term in query.split()):
        return query
    return " ".join([query, *(f"repo:{repo}" for repo in repos)])
