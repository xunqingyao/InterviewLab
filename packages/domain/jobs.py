from __future__ import annotations

from domain.enums import JobStatus
from domain.errors import ActionNotAllowed, StateConflict

CLAIMABLE = {JobStatus.QUEUED, JobStatus.RETRY_WAIT}
TERMINAL = {JobStatus.SUCCEEDED, JobStatus.DEAD, JobStatus.CANCELLED}


def assert_claimable(status: JobStatus, attempt_count: int, max_attempts: int) -> None:
    if status not in CLAIMABLE:
        raise StateConflict(f"job is not claimable from {status}")
    if attempt_count >= max_attempts:
        raise StateConflict("job exceeded max attempts")


def assert_heartbeat(*, status: JobStatus, owner_ok: bool, epoch_ok: bool, lease_valid: bool) -> None:
    if not (status is JobStatus.RUNNING and owner_ok and epoch_ok and lease_valid):
        raise ActionNotAllowed("lease lost; stop execution")


def assert_fence(*, status: JobStatus, owner_ok: bool, epoch_ok: bool, lease_valid: bool) -> None:
    if not (status is JobStatus.RUNNING and owner_ok and epoch_ok and lease_valid):
        raise ActionNotAllowed("fencing token rejected")
