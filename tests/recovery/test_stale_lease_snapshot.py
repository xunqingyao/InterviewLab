import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from db.jobs_repo import claim_one, fence, heartbeat, reclaim_expired
from db.models import Job, User
from db.session import SessionLocal
from domain.errors import ActionNotAllowed
from tests.integration.test_answer_idempotency import reset_db


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [fence, heartbeat], ids=["result_commit", "heartbeat"])
async def test_old_attempt_cannot_use_cached_lease_after_same_worker_id_reclaims(operation):
    await reset_db()
    async with SessionLocal() as db:
        user = User(display_name="lease-test")
        db.add(user)
        await db.flush()
        job = Job(
            owner_id=user.id,
            kind="GENERATE_QUESTION",
            dedupe_key=f"lease:{uuid.uuid4()}",
            status="QUEUED",
            payload_json={},
        )
        db.add(job)
        await db.commit()
    async with SessionLocal() as old_db:
        old = await claim_one(old_db, "reused-worker-id")
        assert old is not None
        old_epoch = old.lease_epoch
        # old_db deliberately keeps its loaded, still apparently valid Job object.
        async with SessionLocal() as new_db:
            await new_db.execute(
                update(Job)
                .where(Job.id == old.id)
                .values(
                    status="RETRY_WAIT",
                    available_at=datetime.now(UTC) - timedelta(seconds=1),
                    lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await new_db.commit()
            new = await claim_one(new_db, "reused-worker-id")
            assert new is not None and new.id == old.id and new.lease_epoch > old_epoch
        with pytest.raises(ActionNotAllowed):
            await operation(old_db, old, "reused-worker-id", old_epoch)


@pytest.mark.asyncio
async def test_late_failure_from_old_attempt_does_not_requeue_new_attempt(monkeypatch):
    import db.handlers as handlers

    await reset_db()
    async with SessionLocal() as db:
        user = User(display_name="late-failure-test")
        db.add(user)
        await db.flush()
        job = Job(
            owner_id=user.id,
            kind="GENERATE_QUESTION",
            dedupe_key=f"late:{uuid.uuid4()}",
            status="QUEUED",
            payload_json={},
        )
        db.add(job)
        await db.commit()

    async def lose_lease_then_fail(db, job, worker_id, epoch):
        async with SessionLocal() as other:
            await other.execute(
                update(Job)
                .where(Job.id == job.id)
                .values(status="RETRY_WAIT", available_at=datetime.now(UTC) - timedelta(seconds=1))
            )
            await other.commit()
            replacement = await claim_one(other, "new-worker")
            assert replacement.id == job.id and replacement.lease_epoch > epoch
        raise RuntimeError("old model request failed after ownership changed")

    monkeypatch.setattr(handlers, "handle_generate_question", lose_lease_then_fail)
    async with SessionLocal() as db:
        old = await claim_one(db, "old-worker")
        try:
            await handlers.process_job(db, old, "old-worker")
        except RuntimeError:
            pass
    async with SessionLocal() as db:
        current = await db.get(Job, job.id)
        assert current.status == "RUNNING"
        assert current.lease_owner == "new-worker"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled, expected", [(False, "DEAD"), (True, "CANCELLED")])
async def test_expired_unclaimable_jobs_become_terminal_instead_of_sticking_in_retry(cancelled, expected):
    await reset_db()
    async with SessionLocal() as db:
        user = User(display_name="terminal-lease-test")
        db.add(user)
        await db.flush()
        job = Job(
            owner_id=user.id,
            kind="GENERATE_QUESTION",
            dedupe_key=f"terminal:{uuid.uuid4()}",
            status="RUNNING",
            payload_json={},
            attempt_count=3,
            max_attempts=3,
            cancel_requested_at=datetime.now(UTC) if cancelled else None,
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        db.add(job)
        await db.commit()
        job_id = job.id
    async with SessionLocal() as db:
        await reclaim_expired(db)
        current = await db.get(Job, job_id)
        assert current.status == expected
        assert await claim_one(db, "replacement") is None
