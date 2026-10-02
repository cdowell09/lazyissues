from lazyissues.models import CloseReason, Issue
from lazyissues.move_planner import AddLabel, Close, RemoveLabels
from lazyissues.move_tracker import MoveTracker, Rejected

ISSUE = Issue("o/r", 1, "One", "https://github.com/o/r/issues/1", labels=("todo",))
TO_DOING = [AddLabel("doing"), RemoveLabels(("todo",))]
TO_REVIEW = [AddLabel("review"), RemoveLabels(("doing",))]


def test_an_issue_has_one_move_in_flight_at_a_time():
    moves = MoveTracker()
    assert moves.start(ISSUE, "doing")
    assert moves.pending(ISSUE.key) == "doing"
    assert not moves.start(ISSUE, "review")
    assert moves.start(Issue("o/r", 2, "Two", "u"), "review")  # other issues move freely

    moves.confirm(ISSUE.key, TO_DOING, at=5.0)
    assert moves.pending(ISSUE.key) is None
    assert moves.start(ISSUE, "review")


def test_a_read_requested_before_a_confirmed_move_cannot_overwrite_its_status():
    moves = MoveTracker()
    assert moves.settle(ISSUE, requested_at=1.0) == ISSUE
    moves.start(ISSUE, "doing")
    moves.confirm(ISSUE.key, TO_DOING, at=5.0)

    assert moves.settle(ISSUE, requested_at=4.0).labels == ("doing",)
    assert moves.settle(ISSUE, requested_at=6.0) == ISSUE
    other = Issue("o/r", 2, "Two", "u", labels=("todo",))
    assert moves.settle(other, requested_at=4.0) == other


def test_a_stale_read_of_an_issue_keeps_every_move_confirmed_after_it():
    moves = MoveTracker()
    moves.start(ISSUE, "doing")
    moves.confirm(ISSUE.key, TO_DOING, at=5.0)
    moves.start(ISSUE, "review")
    moves.confirm(ISSUE.key, TO_REVIEW, at=8.0)
    renamed = Issue("o/r", 1, "Renamed", ISSUE.url, labels=("todo", "bug"))

    # Requested before both moves: both apply, over the read's other fields.
    assert moves.settle(renamed, requested_at=4.0).labels == ("bug", "review")
    # Requested between them: the read already has the first move.
    between = Issue("o/r", 1, "One", ISSUE.url, labels=("doing",))
    assert moves.settle(between, requested_at=6.0).labels == ("review",)
    # Requested after both: the read is the truth, even if GitHub changed since.
    assert moves.settle(renamed, requested_at=9.0) == renamed


def test_a_rejected_move_changes_nothing_and_waits_to_be_dismissed():
    moves = MoveTracker()
    moves.start(ISSUE, "Close as completed")
    moves.reject(ISSUE.key, "Resource not accessible by integration")

    assert moves.pending(ISSUE.key) is None
    assert moves.rejected == [
        Rejected(ISSUE, "Close as completed", "Resource not accessible by integration")
    ]
    assert moves.settle(ISSUE, requested_at=0.0) == ISSUE

    moves.dismiss(moves.rejected[0])
    assert moves.rejected == []


def test_a_confirmed_close_survives_a_stale_read():
    moves = MoveTracker()
    moves.start(ISSUE, "Close as completed")
    moves.confirm(ISSUE.key, [Close(CloseReason.COMPLETED)], at=5.0)
    assert moves.settle(ISSUE, requested_at=4.0).closed
