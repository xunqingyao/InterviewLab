from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from db.models import Base
from db.seed import seed
from db.session import engine
from worker.runner import drain_jobs


async def reset_db():
    await engine.dispose()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_open_turn_per_session ON interview_turns (session_id) WHERE status IN ('RESERVED', 'READY')"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_final_answer_per_turn ON messages (turn_id) WHERE kind = 'ANSWER'"))
        await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_question_per_turn ON messages (turn_id) WHERE kind = 'QUESTION'"))
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS langgraph"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_tsv ON knowledge_chunks USING gin (tsv)"))
    await seed()


async def prepare_session(client: AsyncClient) -> dict:
    resume = await client.post("/api/v1/resumes", data={"text": "梁开新，Python 后端。主导 InterviewLab，做过 RAG 评测和任务队列。优化了效果。"})
    assert resume.status_code == 200, resume.text
    rid = resume.json()["id"]
    vid = resume.json()["versions"][0]["id"]
    confirm = await client.post(f"/api/v1/resumes/{rid}/versions", json={"text": resume.json()["versions"][0]["text"]})
    assert confirm.status_code == 200
    jd = await client.post("/api/v1/job-profiles", json={"title": "AI 应用 / Python 后端", "text": "必须熟悉 FastAPI、PostgreSQL、幂等与 Agent 评测。"})
    jid = jd.json()["id"]
    jvid = jd.json()["versions"][0]["id"]
    await client.post(f"/api/v1/job-profiles/{jid}/versions", json={"text": jd.json()["versions"][0]["text"]})
    created = await client.post(
        "/api/v1/interviews",
        json={"resume_version_id": confirm.json()["id"], "job_profile_version_id": (await client.post(f"/api/v1/job-profiles/{jid}/versions", json={"text": jd.json()["versions"][0]["text"]})).json()["id"], "mode": "STRICT", "duration_minutes": 15},
    )
    # job profile confirm creates new version; use latest from second confirm? Let's recapture.
    return {"resume": resume.json(), "confirm": confirm.json(), "jd": jd.json(), "created": created}


@pytest.mark.asyncio
async def test_answer_idempotency_and_chain():
    await reset_db()
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        resume = await client.post("/api/v1/resumes", json={"text": "主导任务队列与 RAG 评测，优化了效果。"})
        rid = resume.json()["id"]
        confirmed = await client.post(f"/api/v1/resumes/{rid}/versions", json={"text": resume.json()["versions"][0]["text"]})
        jd = await client.post("/api/v1/job-profiles", json={"title": "Python 后端", "text": "必须熟悉幂等、PostgreSQL、评测。"})
        jid = jd.json()["id"]
        jconf = await client.post(f"/api/v1/job-profiles/{jid}/versions", json={"text": jd.json()["versions"][0]["text"]})
        session = await client.post(
            "/api/v1/interviews",
            json={
                "resume_version_id": confirmed.json()["id"],
                "job_profile_version_id": jconf.json()["id"],
                "mode": "STRICT",
                "duration_minutes": 15,
            },
        )
        assert session.status_code == 200, session.text
        sid = session.json()["id"]
        started = await client.post(f"/api/v1/interviews/{sid}/start", json={"expected_state_version": 0})
        assert started.status_code == 200, started.text
        await drain_jobs(20)
        snap = await client.get(f"/api/v1/interviews/{sid}")
        body = snap.json()
        assert body["current_question"]
        turn_id = body["current_question"]["turn_id"]
        payload = {"turn_id": turn_id, "text": "我会先固定测试集和生成配置，再比较两种检索方案。", "expected_state_version": body["state_version"]}
        headers = {"Idempotency-Key": "answer-submit-001"}
        first = await client.post(f"/api/v1/interviews/{sid}/answers", json=payload, headers=headers)
        assert first.status_code == 200
        second = await client.post(f"/api/v1/interviews/{sid}/answers", json=payload, headers=headers)
        assert second.status_code == 200
        assert first.json()["answer_message_id"] == second.json()["answer_message_id"]
        mismatch = await client.post(
            f"/api/v1/interviews/{sid}/answers",
            json={**payload, "text": "another"},
            headers=headers,
        )
        assert mismatch.status_code == 409
        await drain_jobs(20)
        snap2 = await client.get(f"/api/v1/interviews/{sid}")
        assert snap2.json()["state_version"] >= first.json()["state_version"]
