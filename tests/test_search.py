from datetime import date

from lazyissues.search import scoped, with_states


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
    for query in ("is:closed label:bug", "bug is:open", "state:closed"):
        assert with_states(query, date(2026, 9, 18)) == [query]
