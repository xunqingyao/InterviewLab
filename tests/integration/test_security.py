from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from db.commands import create_auth_session
from db.models import Job, User
from db.session import SessionLocal
from tests.integration.test_answer_idempotency import reset_db
from worker.runner import drain_jobs


async def _start_session(client: AsyncClient) -> dict:
    resume = await client.post("/api/v1/resumes", json={"text": "梁开新，Python 后端。主导过任务队列。"})
    confirmed = await client.post(
        f"/api/v1/resumes/{resume.json()['id']}/versions",
        json={"text": resume.json()["versions"][0]["text"]},
    )
    jd = await client.post("/api/v1/job-profiles", json={"title": "后端", "text": "必须熟悉幂等。"})
    jconf = await client.post(
        f"/api/v1/job-profiles/{jd.json()['id']}/versions",
        json={"text": jd.json()["versions"][0]["text"]},
    )
    session = await client.post(
        "/api/v1/interviews",
        json={"resume_version_id": confirmed.json()["id"], "job_profile_version_id": jconf.json()["id"]},
    )
    sid = session.json()["id"]
    await client.post(f"/api/v1/interviews/{sid}/start", json={"expected_state_version": 0})
    await drain_jobs(20)
    snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
    return {"sid": sid, "snap": snap, "resume_id": resume.json()["id"]}


@pytest.mark.asyncio
async def test_csrf_and_origin_and_cross_owner():
    await reset_db()
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        login = await client.post("/auth/dev-login")
        csrf = login.json()["csrf_token"]
        denied = await client.post("/api/v1/resumes", json={"text": "需要 csrf"})
        assert denied.status_code == 403
        origin = await client.post(
            "/api/v1/resumes",
            json={"text": "恶意来源"},
            headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
        )
        assert origin.status_code == 403
        ok = await client.post("/api/v1/resumes", json={"text": "合法来源"}, headers={"X-CSRF-Token": csrf})
        assert ok.status_code == 200

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        started = await _start_session(client)
        sid = started["sid"]

        async with SessionLocal() as db:
            other = User(display_name="other", is_dev=False)
            db.add(other)
            await db.flush()
            auth = await create_auth_session(db, other)
            token = auth._raw_token  # type: ignore[attr-defined]
            other_csrf = auth.csrf_token
            await db.commit()

        stolen = await client.get(
            f"/api/v1/interviews/{sid}",
            cookies={"session": token, "csrf_token": other_csrf},
        )
        assert stolen.status_code == 403


@pytest.mark.asyncio
async def test_delete_freezes_parse_job():
    await reset_db()
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        files = {"file": ("resume.pdf", b"%PDF-1.1\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n", "application/pdf")}
        created = await client.post("/api/v1/resumes", files=files)
        rid = created.json()["id"]
        assert created.json()["status"] == "PENDING"
        deleted = await client.delete(f"/api/v1/resumes/{rid}")
        assert deleted.status_code == 200
        async with SessionLocal() as db:
            jobs = (await db.execute(select(Job).where(Job.kind == "PARSE_RESUME"))).scalars().all()
            assert jobs
            assert all(j.status == "CANCELLED" or j.cancel_requested_at is not None for j in jobs)
        gone = await client.get(f"/api/v1/resumes/{rid}")
        assert gone.status_code == 404


@pytest.mark.asyncio
async def test_review_supersedes_and_new_report():
    await reset_db()
    from api.main import app
    from db.models import Assessment, Report

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        started = await _start_session(client)
        sid = started["sid"]
        snap = started["snap"]
        await client.post(
            f"/api/v1/interviews/{sid}/answers",
            json={
                "turn_id": snap["current_question"]["turn_id"],
                "text": "我会先固定测试集和生成配置，再比较两种检索方案。",
                "expected_state_version": snap["state_version"],
            },
            headers={"Idempotency-Key": "rev-1"},
        )
        await drain_jobs(20)
        snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
        await client.post(f"/api/v1/interviews/{sid}/finish", json={"expected_state_version": snap["state_version"]})
        await drain_jobs(20)
        first = await client.get(f"/api/v1/interviews/{sid}/reports/latest")
        assert first.status_code == 200
        rev1 = first.json()["revision"]
        async with SessionLocal() as db:
            assessment = (await db.execute(select(Assessment))).scalars().first()
            assert assessment is not None
            aid = str(assessment.id)
        review = await client.post(f"/api/v1/assessments/{aid}/reviews", json={"reason": "引用不完整，请重评。"})
        assert review.status_code == 200, review.text
        await drain_jobs(20)
        async with SessionLocal() as db:
            rows = (await db.execute(select(Assessment).order_by(Assessment.revision))).scalars().all()
            assert len(rows) >= 2
            latest = rows[-1]
            assert latest.supersedes_id is not None
            reports = (await db.execute(select(Report).order_by(Report.revision))).scalars().all()
            assert reports[-1].revision >= rev1
        second = await client.get(f"/api/v1/interviews/{sid}/reports/latest")
        assert second.json()["revision"] >= rev1
