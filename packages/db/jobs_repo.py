from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Job
from db.settings import settings
from domain.enums import JobStatus
from domain.errors import ActionNotAllowed, ResourceNotFound
from domain.jobs import assert_fence, assert_heartbeat

CLAIM_SQL = """
WITH candidate AS (
    SELECT id
    FROM jobs
    WHERE status IN ('QUEUED', 'RETRY_WAIT')
      AND available_at <= clock_timestamp()
      AND attempt_count < max_attempts
      AND cancel_requested_at IS NULL
    ORDER BY priority DESC, available_at, id
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE jobs AS claimed_job
SET status = 'RUNNING',
    lease_owner = :worker_id,
    lease_epoch = claimed_job.lease_epoch + 1,
    lease_expires_at = clock_timestamp() + make_interval(secs => :lease_seconds),
    heartbeat_at = clock_timestamp(),
    attempt_count = claimed_job.attempt_count + 1
FROM candidate
WHERE claimed_job.id = candidate.id
RETURNING claimed_job.id
"""


async def claim_one(session: AsyncSession, worker_id: str) -> Job | None:
    result = await session.execute(
        text(CLAIM_SQL),
        {"worker_id": worker_id, "lease_seconds": settings.lease_seconds},
    )
    row = result.first()
    await session.commit()
    if not row:
        return None
    return await session.get(Job, row[0])


async def heartbeat(session: AsyncSession, job: Job, worker_id: str, lease_epoch: int) -> None:
    job = await session.get(Job, job.id, with_for_update=True, populate_existing=True)
    if job is None:
        raise ResourceNotFound("job missing")
    now = datetime.now(UTC)
    lease_valid = bool(job.lease_expires_at and job.lease_expires_at > now)
    try:
        assert_heartbeat(
            status=JobStatus(job.status),
            owner_ok=job.lease_owner == worker_id,
            epoch_ok=job.lease_epoch == lease_epoch,
            lease_valid=lease_valid,
        )
    except ActionNotAllowed:
        raise
    job.heartbeat_at = now
    job.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
    await session.commit()


async def fence(session: AsyncSession, job: Job, worker_id: str, lease_epoch: int) -> Job:
    locked = await session.get(Job, job.id, with_for_update=True, populate_existing=True)
    if locked is None:
        raise ResourceNotFound("job missing")
    now = datetime.now(UTC)
    assert_fence(
        status=JobStatus(locked.status),
        owner_ok=locked.lease_owner == worker_id,
        epoch_ok=locked.lease_epoch == lease_epoch,
        lease_valid=bool(locked.lease_expires_at and locked.lease_expires_at > now),
    )
    return locked


async def reclaim_expired(session: AsyncSession) -> int:
    result = await session.execute(
        text(
            """
            UPDATE jobs
            SET status = CASE
                    WHEN cancel_requested_at IS NOT NULL THEN 'CANCELLED'
                    WHEN attempt_count >= max_attempts THEN 'DEAD'
                    ELSE 'RETRY_WAIT'
                END,
                available_at = clock_timestamp() + interval '3 seconds',
                last_error_code = 'LEASE_EXPIRED'
            WHERE status = 'RUNNING'
              AND lease_expires_at < clock_timestamp()
            """
        )
    )
    await session.commit()
    return result.rowcount or 0
