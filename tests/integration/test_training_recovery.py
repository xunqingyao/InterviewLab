import uuid

import pytest
from sqlalchemy import select

from db.models import Job, Report, TrainingPlan
from db.session import SessionLocal
from domain.schemas import CoachProposal
from domain.training import TrainingProposalError
from tests.integration.test_answer_idempotency import reset_db
from tests.integration.test_design_regressions import start_interview
from worker.runner import drain_jobs


@pytest.mark.asyncio
async def test_training_failure_status_and_retry_preserve_attempt_history(client, monkeypatch):
    await reset_db()
    from db import handlers

    sid = await start_interview(client)
    snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
    await client.post(
        f"/api/v1/interviews/{sid}/finish", json={"expected_state_version": snap["state_version"]}
    )
    await drain_jobs(20)
    # Explicit weak-evidence fixture; no live model calls or production data.
    async with SessionLocal() as db:
        report = (await db.execute(select(Report).where(Report.session_id == uuid.UUID(sid)))).scalar_one()
        report.body_json = {
            **report.body_json,
            "next_training": ["req_01"],
            "coverage": [
                {"skill_id": "req_01", "label": "任务处理", "levels": [1], "evidence": []},
            ],
        }
        await db.commit()

    async def invalid(*args, **kwargs):
        raise TrainingProposalError("INVALID_TARGET", "训练目标必须来自报告待训练要求，且不能重复。")

    monkeypatch.setattr(handlers, "propose_training", invalid)
    initial = (await client.post("/api/v1/training-plans", json={"session_id": sid})).json()["job_id"]
    await drain_jobs(3)
    status = (await client.get(f"/api/v1/training-plans/job?session_id={sid}")).json()["job"]
    assert status["status"] == "DEAD"
    assert status["attempt_count"] == 3
    assert "训练目标" in status["error_reason"]
    retry = (await client.post("/api/v1/training-plans", json={"session_id": sid})).json()["job_id"]
    assert retry == initial
    await client.post("/api/v1/training-plans", json={"session_id": sid})
    async with SessionLocal() as db:
        job = await db.get(Job, uuid.UUID(initial))
        assert job.attempt_count == 3
        assert job.max_attempts == 6
        assert job.status == "QUEUED"

    async def valid(*args, **kwargs):
        return CoachProposal(
            targets=[
                dict(requirement_id="req_01", claim_id=None, exercise_hint="具体实现", retest_hint="失败恢复")
            ],
            rationale="补齐弱项",
        )

    monkeypatch.setattr(handlers, "propose_training", valid)
    await drain_jobs(3)
    status = (await client.get(f"/api/v1/training-plans/job?session_id={sid}")).json()["job"]
    assert status["status"] == "SUCCEEDED"
    assert status["attempt_count"] == 4
    assert not status["error_reason"]
    async with SessionLocal() as db:
        plans = (
            (await db.execute(select(TrainingPlan).where(TrainingPlan.session_id == uuid.UUID(sid))))
            .scalars()
            .all()
        )
        assert len(plans) == 1
    assert (await client.get(f"/api/v1/training-plans/job?session_id={uuid.uuid4()}")).status_code == 404
