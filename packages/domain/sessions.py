from __future__ import annotations

from domain.enums import SessionPhase, SessionStatus
from domain.errors import StateConflict

STARTABLE = {SessionStatus.CREATED}
ANSWERABLE = {SessionStatus.ACTIVE}
FINISHABLE = {SessionStatus.ACTIVE}
CANCELLABLE = {SessionStatus.CREATED, SessionStatus.ACTIVE, SessionStatus.CLOSING}
CLOSABLE_TO_COMPLETED = {SessionStatus.CLOSING}

_TRANSITIONS: dict[tuple[SessionStatus, str], SessionStatus] = {
    (SessionStatus.CREATED, "start"): SessionStatus.ACTIVE,
    (SessionStatus.ACTIVE, "finish"): SessionStatus.CLOSING,
    (SessionStatus.ACTIVE, "soft_limit"): SessionStatus.CLOSING,
    (SessionStatus.ACTIVE, "agenda_done"): SessionStatus.CLOSING,
    (SessionStatus.CLOSING, "report_snapshot_published"): SessionStatus.COMPLETED,
    (SessionStatus.CREATED, "cancel"): SessionStatus.CANCELLED,
    (SessionStatus.ACTIVE, "cancel"): SessionStatus.CANCELLED,
    (SessionStatus.CLOSING, "cancel"): SessionStatus.CANCELLED,
    (SessionStatus.CREATED, "unrecoverable_error"): SessionStatus.FAILED,
    (SessionStatus.ACTIVE, "unrecoverable_error"): SessionStatus.FAILED,
    (SessionStatus.CLOSING, "unrecoverable_error"): SessionStatus.FAILED,
}


def next_status(current: SessionStatus, action: str) -> SessionStatus:
    try:
        return _TRANSITIONS[(current, action)]
    except KeyError as exc:
        raise StateConflict(f"session cannot {action} from {current}") from exc


def assert_expected_version(actual: int, expected: int | None) -> None:
    if expected is not None and actual != expected:
        raise StateConflict(
            "state_version mismatch",
            details={"actual": actual, "expected": expected},
        )


def phase_after_start() -> SessionPhase:
    return SessionPhase.PLANNING


def phase_after_question() -> SessionPhase:
    return SessionPhase.AWAITING_ANSWER


def phase_after_answer() -> SessionPhase:
    return SessionPhase.GENERATING


def phase_idle() -> SessionPhase:
    return SessionPhase.IDLE
