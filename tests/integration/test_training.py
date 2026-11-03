from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from db.models import RubricSnapshot, TrainingItem
from db.session import SessionLocal
from tests.integration.test_answer_idempotency import reset_db
from worker.runner import drain_jobs


@pytest.mark.asyncio
async def test_training_plan_uses_distinct_retest_and_hinted_is_not_independent():
    await reset_db()
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        resume = await client.post("/api/v1/resumes", json={"text": "主导任务队列与 RAG 评测，做过幂等键。"})
        confirmed = await client.post(
            f"/api/v1/resumes/{resume.json()['id']}/versions", json={"text": resume.json()["versions"][0]["text"]}
        )
        jd = await client.post("/api/v1/job-profiles", json={"title": "Python 后端", "text": "必须熟悉幂等、PostgreSQL、评测。"})
        jconf = await client.post(f"/api/v1/job-profiles/{jd.json()['id']}/versions", json={"text": jd.json()["versions"][0]["text"]})
        session = await client.post(
            "/api/v1/interviews",
            json={
                "resume_version_id": confirmed.json()["id"],
                "job_profile_version_id": jconf.json()["id"],
                "mode": "STRICT",
                "duration_minutes": 15,
            },
        )
        sid = session.json()["id"]
        await client.post(f"/api/v1/interviews/{sid}/start", json={"expected_state_version": 0})
        await drain_jobs(20)
        snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
        too_early = await client.post("/api/v1/training-plans", json={"session_id": sid})
        assert too_early.status_code == 403
        await client.post(
            f"/api/v1/interviews/{sid}/answers",
            json={
                "turn_id": snap["current_question"]["turn_id"],
                "text": "我们做了幂等键，但还没固定测试集。",
                "expected_state_version": snap["state_version"],
            },
            headers={"Idempotency-Key": "train-ans-1"},
        )
        await drain_jobs(20)
        snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
        await client.post(f"/api/v1/interviews/{sid}/finish", json={"expected_state_version": snap["state_version"]})
        await drain_jobs(30)
        report = (await client.get(f"/api/v1/interviews/{sid}/reports/latest")).json()
        assert report["body"]["next_training"]
        assert report["training"] == {"eligible": True, "reason": None}
        created = await client.post("/api/v1/training-plans", json={"session_id": sid})
        assert created.status_code == 200, created.text
        await drain_jobs(20)
        plans = (await client.get("/api/v1/training-plans")).json()["items"]
        assert plans
        plan = next(item for item in plans if item["session_id"] == sid)
        exercises = [item for item in plan["items"] if item["kind"] == "EXERCISE"]
        retests = [item for item in plan["items"] if item["kind"] == "RETEST"]
        assert exercises
        assert retests
        exercise = exercises[0]
        retest = next(item for item in retests if item["template_key"] == exercise["variant_template_key"])
        assert exercise["id"] != retest["id"]
        assert exercise["template_key"] != retest["template_key"]
        assert exercise["question_text"] != retest["question_text"]
        assert not exercise["question_text"].startswith("用户重试")
        async with SessionLocal() as db:
            item = await db.get(TrainingItem, uuid.UUID(exercise["id"]))
            frozen = await db.get(RubricSnapshot, item.rubric_snapshot_id)
            assert frozen.schema_version == "v2"
            assert frozen.criteria_json["scope"]["question"] == item.question_text
            # Simulate an older, ungraded training item without a snapshot.
            item.rubric_snapshot_id = None
            await db.commit()
        hinted = await client.post(
            f"/api/v1/exercises/{exercise['id']}/submissions",
            json={"text": "同一测试集、同一生成配置，只改检索，失败时看对照指标。", "hint_level": "EXAMPLE"},
        )
        assert hinted.status_code == 200
        assert hinted.json()["independent"] is False
        blocked = await client.post(
            f"/api/v1/exercises/{retest['id']}/submissions",
            json={"text": "复测也想看示范。", "hint_level": "EXAMPLE"},
        )
        assert blocked.status_code == 403
        await drain_jobs(20)
        async with SessionLocal() as db:
            item = await db.get(TrainingItem, uuid.UUID(exercise["id"]))
            assert item.rubric_snapshot_id is not None
        skills = (await client.get("/api/v1/skills")).json()["items"]
        exercise_rows = [row for row in skills if row.get("source") == "exercise"]
        assert exercise_rows
        assert all(row["hint_level"] == "EXAMPLE" for row in exercise_rows)
        assert all(row["independent"] is False for row in exercise_rows)
