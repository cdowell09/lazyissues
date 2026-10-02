from lazyissues.search import scoped


def test_scopes_to_repo_set():
    assert scoped("is:open", ["a/x", "a/y"]) == "is:open repo:a/x repo:a/y"


def test_keeps_a_query_that_names_its_own_scope():
    for query in ("is:open repo:b/z", "org:acme bug", "user:me is:open"):
        assert scoped(query, ["a/x"]) == query
