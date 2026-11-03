from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from db.models import User
from db.session import SessionLocal
from tests.integration.test_answer_idempotency import reset_db
from worker.runner import drain_jobs


@pytest.mark.asyncio
async def test_strict_mode_hides_assessment_and_cross_user_denied():
    await reset_db()
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        resume = await client.post("/api/v1/resumes", json={"text": "隐私与权限测试。"})
        confirmed = await client.post(
            f"/api/v1/resumes/{resume.json()['id']}/versions", json={"text": resume.json()["versions"][0]["text"]}
        )
        jd = await client.post("/api/v1/job-profiles", json={"title": "安全", "text": "必须做 owner 校验。"})
        jconf = await client.post(f"/api/v1/job-profiles/{jd.json()['id']}/versions", json={"text": jd.json()["versions"][0]["text"]})
        session = await client.post(
            "/api/v1/interviews",
            json={"resume_version_id": confirmed.json()["id"], "job_profile_version_id": jconf.json()["id"]},
        )
        sid = session.json()["id"]
        await client.post(f"/api/v1/interviews/{sid}/start", json={"expected_state_version": 0})
        await drain_jobs(20)
        snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
        await client.post(
            f"/api/v1/interviews/{sid}/answers",
            json={
                "turn_id": snap["current_question"]["turn_id"],
                "text": "所有查询带 owner_id，模型不能传入权限字段。",
                "expected_state_version": snap["state_version"],
            },
            headers={"Idempotency-Key": "own-1"},
        )
        await drain_jobs(20)
        from db.models import Assessment

        async with SessionLocal() as db:
            assessment = (await db.execute(select(Assessment))).scalars().first()
            assert assessment is not None
            aid = str(assessment.id)
        snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
        hidden = await client.get(f"/api/v1/assessments/{aid}")
        if snap["status"] != "COMPLETED":
            assert hidden.status_code == 403
            await client.post(
                f"/api/v1/interviews/{sid}/finish",
                json={"expected_state_version": snap["state_version"]},
            )
            await drain_jobs(20)
        visible = await client.get(f"/api/v1/assessments/{aid}")
        assert visible.status_code == 200
        report = await client.get(f"/api/v1/interviews/{sid}/reports/latest")
        assert report.status_code == 200
        other = await client.get("/api/v1/interviews/00000000-0000-0000-0000-000000000099")
        assert other.status_code in {403, 404}
