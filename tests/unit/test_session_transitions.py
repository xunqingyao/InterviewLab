from domain.enums import SessionStatus
from domain.errors import StateConflict
from domain.sessions import next_status


def test_happy_path_transitions():
    assert next_status(SessionStatus.CREATED, "start") is SessionStatus.ACTIVE
    assert next_status(SessionStatus.ACTIVE, "finish") is SessionStatus.CLOSING
    assert next_status(SessionStatus.CLOSING, "report_snapshot_published") is SessionStatus.COMPLETED


def test_cancel_from_active():
    assert next_status(SessionStatus.ACTIVE, "cancel") is SessionStatus.CANCELLED


def test_illegal_transition():
    try:
        next_status(SessionStatus.COMPLETED, "start")
        raise AssertionError("should fail")
    except StateConflict as exc:
        assert exc.code == "STATE_CONFLICT"
