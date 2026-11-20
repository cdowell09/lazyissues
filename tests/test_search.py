from datetime import date

from lazyissues.search import (
    asks_for_closed,
    closed_only,
    scoped,
    targets_only_repos,
    with_states,
)


def test_scopes_to_repo_set():
    assert scoped("is:open", ["a/x", "a/y"]) == "is:open repo:a/x repo:a/y"


def test_keeps_a_query_that_names_its_own_scope():
    for query in ("is:open repo:b/z", "org:acme bug", "user:me is:open"):
        assert scoped(query, ["a/x"]) == query


def test_reads_open_issues_and_those_closed_since_a_date():
    assert with_states("label:bug", None) == ["label:bug is:open"]
    assert with_states("label:bug", date(2026, 9, 18)) == [
        "label:bug is:open",
        "label:bug is:closed closed:>=2026-09-18",
    ]


def test_keeps_a_query_that_names_its_own_state():
    for query in ("is:closed label:bug", "bug is:open", "state:closed", "-is:open"):
        assert with_states(query, date(2026, 9, 18)) == [query]


def test_knows_a_query_that_asks_for_closed_issues():
    for query in ("is:closed", "label:bug state:closed", "-is:open", "-state:open"):
        assert asks_for_closed(query)
    for query in ("label:bug", "is:open", "state:open", "is:issue"):
        assert not asks_for_closed(query)


def test_closed_only_builds_closed_query_or_respects_explicit_state():
    since = date(2026, 9, 18)
    assert closed_only("label:bug", since) == ["label:bug is:closed closed:>=2026-09-18"]
    assert closed_only("is:open label:bug", since) == []
    assert closed_only("state:open label:bug", since) == []
    assert closed_only("is:closed label:bug", since) == ["is:closed label:bug"]
    assert closed_only("state:closed label:bug", since) == ["state:closed label:bug"]
    assert closed_only("-is:open label:bug", since) == ["-is:open label:bug"]


def test_targets_only_repos_checks_repo_qualifiers_case_insensitively():
    assert targets_only_repos('is:issue repo:o/a milestone:"v1"', ["O/A", "o/b"])
    assert targets_only_repos("repo:o/a repo:O/B", ["o/a", "o/b"])
    assert not targets_only_repos("repo:o/a repo:o/c", ["o/a", "o/b"])
    assert not targets_only_repos("is:issue assignee:@me", ["o/a"])
    assert not targets_only_repos("repo:o/a", [])
