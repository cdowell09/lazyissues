import pytest

from lazyissues.models import Issue
from lazyissues.store import SNAPSHOT_VERSION as V
from lazyissues.store import IssueStore, snapshot_path


def issue(number: int, title: str = "An issue", **fields) -> Issue:
    return Issue(
        repo="octo/repo",
        number=number,
        title=title,
        url=f"https://github.com/octo/repo/issues/{number}",
        **fields,
    )


def test_a_new_store_opens_with_the_last_snapshot(tmp_path):
    path = tmp_path / "snapshot.json"
    issues = [
        issue(1, assignees=("octo", "sam")),
        issue(2, labels=("bug",), closed=True, project_statuses={"octo/3": "In Progress"}),
    ]
    IssueStore(path).replace(issues, requested_at=1.0)

    assert IssueStore(path).issues == issues


def test_a_missing_snapshot_opens_empty(tmp_path):
    assert IssueStore(tmp_path / "snapshot.json").issues == []


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        '{"version": 0, "issues": []}',
        f'{{"version": {V}, "issues": [{{"repo": "octo/repo", "number": 1}}]}}',
        f'{{"version": {V}, "issues": [{{"repo": "octo/repo", "number": 1, "title": "t",'
        ' "url": "u", "removed_field": 1}]}',
        f'{{"version": {V}, "issues": null}}',
    ],
    ids=["corrupt", "wrong-shape", "old-version", "missing-field", "unknown-field", "no-list"],
)
def test_an_unreadable_snapshot_is_discarded_and_rebuilt(tmp_path, content):
    path = tmp_path / "snapshot.json"
    path.write_text(content, encoding="utf-8")

    store = IssueStore(path)
    assert store.issues == []

    store.replace([issue(1)], requested_at=1.0)
    assert IssueStore(path).issues == [issue(1)]


def test_a_read_requested_before_the_loaded_one_is_ignored(tmp_path):
    path = tmp_path / "snapshot.json"
    store = IssueStore(path)
    assert store.replace([issue(1, "newer")], requested_at=2.0)

    assert not store.replace([issue(1, "older")], requested_at=1.0)
    assert store.issues == [issue(1, "newer")]
    assert IssueStore(path).issues == [issue(1, "newer")]


def test_each_repo_set_has_its_own_snapshot(tmp_path):
    cache = tmp_path / "not-yet-created"
    IssueStore(snapshot_path(cache, ["octo/a", "octo/b"])).replace([issue(1)], requested_at=1.0)

    assert IssueStore(snapshot_path(cache, ["octo/b", "octo/a"])).issues == [issue(1)]
    assert IssueStore(snapshot_path(cache, ["octo/a"])).issues == []


def test_an_unwritable_cache_still_updates_the_loaded_issues(tmp_path):
    blocker = tmp_path / "cache"
    blocker.write_text("a file where the cache directory should be", encoding="utf-8")
    store = IssueStore(blocker / "snapshot.json")

    assert store.replace([issue(1)], requested_at=1.0)
    assert store.issues == [issue(1)]


def test_a_store_without_a_snapshot_lives_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = IssueStore()
    assert store.issues == []

    assert store.replace([issue(1)], requested_at=1.0)
    assert store.issues == [issue(1)]
    assert list(tmp_path.iterdir()) == []
