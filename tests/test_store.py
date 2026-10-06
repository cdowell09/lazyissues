import asyncio

import pytest

from lazyissues.models import Issue
from lazyissues.move_planner import AddLabel, RemoveLabels
from lazyissues.move_tracker import MoveTracker
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
    store.replace([issue(1, "newer")], requested_at=2.0)

    store.replace([issue(1, "older")], requested_at=1.0)
    assert store.issues == [issue(1, "newer")]
    assert IssueStore(path).issues == [issue(1, "newer")]


def test_each_view_and_repo_set_has_its_own_snapshot(tmp_path):
    cache = tmp_path / "not-yet-created"
    path = snapshot_path(cache, "team", ["octo/a", "octo/b"])
    IssueStore(path).replace([issue(1)], requested_at=1.0)

    assert IssueStore(snapshot_path(cache, "team", ["octo/b", "octo/a"])).issues == [issue(1)]
    assert IssueStore(snapshot_path(cache, "team", ["octo/a"])).issues == []
    assert IssueStore(snapshot_path(cache, "my-work", ["octo/a", "octo/b"])).issues == []


def test_an_unwritable_cache_still_updates_the_loaded_issues(tmp_path):
    blocker = tmp_path / "cache"
    blocker.write_text("a file where the cache directory should be", encoding="utf-8")
    store = IssueStore(blocker / "snapshot.json")

    store.replace([issue(1)], requested_at=1.0)
    assert store.issues == [issue(1)]


def test_a_store_without_a_snapshot_lives_in_memory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = IssueStore()
    assert store.issues == []

    store.replace([issue(1)], requested_at=1.0)
    assert store.issues == [issue(1)]
    assert list(tmp_path.iterdir()) == []


async def test_a_refresh_answered_after_a_later_one_is_ignored():
    store = IssueStore()
    gate = asyncio.Event()

    async def slow_read() -> list[Issue]:
        await gate.wait()
        return [issue(1, "older")]

    async def fast_read() -> list[Issue]:
        return [issue(1, "newer")]

    slow = asyncio.create_task(store.refresh(slow_read()))
    await asyncio.sleep(0)  # the slow read is requested first
    await store.refresh(fast_read())
    gate.set()
    await slow

    assert store.issues == [issue(1, "newer")]


def test_a_read_requested_before_a_confirmed_move_keeps_that_issues_new_status(tmp_path):
    moves = MoveTracker()
    store = IssueStore(tmp_path / "snapshot.json", moves)
    store.replace([issue(1, labels=("todo",)), issue(2)], requested_at=1.0)
    moves.start(store.issues[0], "doing")
    moves.confirm(store.issues[0].key, [AddLabel("doing"), RemoveLabels(("todo",))], at=5.0)

    # Requested before the move was confirmed, answered after it.
    store.replace([issue(1, "renamed", labels=("todo",)), issue(2, "renamed")], 4.0)

    assert store.issues == [issue(1, "renamed", labels=("doing",)), issue(2, "renamed")]


def test_a_confirmed_move_shows_on_the_loaded_issues_and_their_snapshot(tmp_path):
    path = tmp_path / "snapshot.json"
    moves = MoveTracker()
    store = IssueStore(path, moves)
    store.replace([issue(1, labels=("todo",)), issue(2)], requested_at=1.0)
    moves.start(store.issues[0], "doing")
    moves.confirm(store.issues[0].key, [AddLabel("doing"), RemoveLabels(("todo",))], at=5.0)

    store.apply_moves()

    assert store.issues == [issue(1, labels=("doing",)), issue(2)]


def test_update_replaces_a_loaded_issue_in_place_and_saves_it(tmp_path):
    path = tmp_path / "snapshot.json"
    store = IssueStore(path)
    store.replace([issue(1), issue(2)], requested_at=1.0)

    assert store.update(issue(1, "Renamed"), written_at=2.0)
    assert not store.update(issue(3), written_at=2.0)  # not loaded here, so not added

    assert [i.title for i in store.issues] == ["Renamed", "An issue"]
    assert IssueStore(path).issues == store.issues


def test_a_read_requested_before_a_write_keeps_the_written_copy(tmp_path):
    store = IssueStore(tmp_path / "snapshot.json")
    store.replace([issue(1), issue(2)], requested_at=1.0)
    store.update(issue(1, "Written"), written_at=5.0)

    store.replace([issue(1, "Stale"), issue(2, "Fresh")], requested_at=4.0)
    assert [i.title for i in store.issues] == ["Written", "Fresh"]

    store.replace([issue(1, "Newer"), issue(2)], requested_at=6.0)
    assert store.issues[0].title == "Newer"


def test_a_move_confirmed_before_a_write_is_not_applied_over_it_again(tmp_path):
    moves = MoveTracker()
    store = IssueStore(tmp_path / "snapshot.json", moves)
    store.replace([issue(1, labels=("todo",))], requested_at=1.0)
    moves.start(store.issues[0], "doing")
    moves.confirm(store.issues[0].key, [AddLabel("doing"), RemoveLabels(("todo",))], at=2.0)
    store.apply_moves()

    # A write after the move removed `doing` again; settling the list must not undo it.
    store.update(issue(1, labels=("bug",)), written_at=3.0)
    store.apply_moves()

    assert store.issues[0].labels == ("bug",)


def test_a_burst_of_changes_saves_one_snapshot_holding_the_last(tmp_path, monkeypatch):
    monkeypatch.setattr("lazyissues.store.SAVE_DELAY", 0.01)
    path = tmp_path / "snapshot.json"

    async def burst() -> None:
        store = IssueStore(path)
        store.replace([issue(1)], requested_at=1.0)
        store.replace([issue(1), issue(2)], requested_at=2.0)
        assert not path.exists()  # waiting for the burst to end
        await asyncio.sleep(0.05)

    asyncio.run(burst())

    assert [i.number for i in IssueStore(path).issues] == [1, 2]


def test_flush_saves_a_pending_snapshot_at_once(tmp_path):
    path = tmp_path / "snapshot.json"

    async def change_and_exit() -> None:
        store = IssueStore(path)
        store.replace([issue(1)], requested_at=1.0)
        store.flush()
        assert IssueStore(path).issues == [issue(1)]

    asyncio.run(change_and_exit())
