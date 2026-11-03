from __future__ import annotations

from domain.enums import TurnStatus
from domain.errors import ActionNotAllowed, StateConflict

OPEN_TURN_STATUSES = {TurnStatus.RESERVED, TurnStatus.READY}


def assert_can_publish(current_open: int) -> None:
    if current_open >= 1:
        raise StateConflict("session already has an open turn")


def assert_ready_for_answer(*, turn_status: TurnStatus, is_current: bool) -> None:
    if turn_status != TurnStatus.READY or not is_current:
        raise ActionNotAllowed("answers can only be submitted to the current READY turn")


def after_answer() -> TurnStatus:
    return TurnStatus.ANSWERED


def after_skip(turn_status: TurnStatus) -> TurnStatus:
    if turn_status != TurnStatus.READY:
        raise StateConflict("only a READY turn can be skipped")
    return TurnStatus.SKIPPED


def abandon(turn_status: TurnStatus) -> TurnStatus:
    if turn_status != TurnStatus.RESERVED:
        raise StateConflict("only a RESERVED turn can be abandoned")
    return TurnStatus.ABANDONED
