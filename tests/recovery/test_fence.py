from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from db.jobs_repo import claim_one, fence, reclaim_expired
from db.models import Job
from db.session import SessionLocal
from domain.errors import ActionNotAllowed
from tests.integration.test_answer_idempotency import reset_db


@pytest.mark.asyncio
async def test_expired_worker_is_fenced():
    await reset_db()
    from api.main import app
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        resume = await client.post("/api/v1/resumes", json={"text": "恢复测试。"})
        confirmed = await client.post(
            f"/api/v1/resumes/{resume.json()['id']}/versions", json={"text": resume.json()["versions"][0]["text"]}
        )
        jd = await client.post("/api/v1/job-profiles", json={"title": "后端", "text": "必须熟悉租约。"})
        jconf = await client.post(f"/api/v1/job-profiles/{jd.json()['id']}/versions", json={"text": jd.json()["versions"][0]["text"]})
        session = await client.post(
            "/api/v1/interviews",
            json={"resume_version_id": confirmed.json()["id"], "job_profile_version_id": jconf.json()["id"]},
        )
        await client.post(f"/api/v1/interviews/{session.json()['id']}/start", json={"expected_state_version": 0})

    async with SessionLocal() as db:
        job = await claim_one(db, "worker-old")
        assert job is not None
        old_epoch = job.lease_epoch
        job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        await db.commit()
    async with SessionLocal() as db:
        n = await reclaim_expired(db)
        assert n >= 1
        stale = await db.get(Job, job.id)
        assert stale.status == "RETRY_WAIT"
        try:
            await fence(db, stale, "worker-old", old_epoch)
            raise AssertionError("old worker must be fenced")
        except ActionNotAllowed:
            pass
        job2 = await claim_one(db, "worker-new")
        assert job2 is not None
        if job2.id == job.id:
            assert job2.lease_epoch != old_epoch


@pytest.mark.asyncio
async def test_killed_evaluator_does_not_double_write():
    await reset_db()
    from api.main import app
    from sqlalchemy import func, select

    from db.models import Assessment, Job as JobModel
    from domain.enums import JobKind, JobStatus
    from worker.runner import drain_jobs

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        resume = await client.post("/api/v1/resumes", json={"text": "恢复测试。我会先固定测试集。"})
        confirmed = await client.post(
            f"/api/v1/resumes/{resume.json()['id']}/versions", json={"text": resume.json()["versions"][0]["text"]}
        )
        jd = await client.post("/api/v1/job-profiles", json={"title": "后端", "text": "必须熟悉租约。"})
        jconf = await client.post(f"/api/v1/job-profiles/{jd.json()['id']}/versions", json={"text": jd.json()["versions"][0]["text"]})
        session = await client.post(
            "/api/v1/interviews",
            json={"resume_version_id": confirmed.json()["id"], "job_profile_version_id": jconf.json()["id"]},
        )
        sid = session.json()["id"]
        await client.post(f"/api/v1/interviews/{sid}/start", json={"expected_state_version": 0})
        await drain_jobs(20)
        snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
        body = {
            "turn_id": snap["current_question"]["turn_id"],
            "text": "我会先固定测试集和生成配置，并用 fencing 防止旧 Worker 写回。",
            "expected_state_version": snap["state_version"],
        }
        first = await client.post(f"/api/v1/interviews/{sid}/answers", json=body, headers={"Idempotency-Key": "rec-1"})
        second = await client.post(f"/api/v1/interviews/{sid}/answers", json=body, headers={"Idempotency-Key": "rec-1"})
        assert first.json()["answer_message_id"] == second.json()["answer_message_id"]

    async with SessionLocal() as db:
        eval_job = (
            await db.execute(select(JobModel).where(JobModel.kind == JobKind.EVALUATE_TURN.value))
        ).scalars().first()
        assert eval_job is not None
        eval_job.status = JobStatus.RUNNING.value
        eval_job.lease_owner = "worker-killed"
        eval_job.lease_epoch = 3
        eval_job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        eval_job.attempt_count = 1
        await db.commit()
        job_id = eval_job.id
        old_epoch = 3
    async with SessionLocal() as db:
        await reclaim_expired(db)
        stale = await db.get(Job, job_id)
        try:
            await fence(db, stale, "worker-killed", old_epoch)
            raise AssertionError("killed worker must be fenced")
        except ActionNotAllowed:
            pass

    import asyncio

    await asyncio.sleep(3.2)
    await drain_jobs(20, worker_id="worker-new")
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count()).select_from(Assessment))).scalar() or 0
        assert count == 1
        row = (await db.execute(select(Assessment))).scalar_one()
        for dim in row.result_json.get("dimensions") or []:
            if dim.get("level") is not None:
                assert dim["level"] != 0 or dim.get("answer_evidence")



