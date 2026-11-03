import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from db.models import InterviewTurn, Job, Report, RubricSnapshot
from db.session import SessionLocal
from db.settings import settings
from domain.enums import JobKind, JobStatus
from tests.integration.test_answer_idempotency import reset_db
from worker.runner import drain_jobs


async def start_interview(client):
    resume = await client.post(
        "/api/v1/resumes", json={"text": "主导 Python 异步任务队列，负责幂等处理和进程重启后的任务恢复。"}
    )
    confirmed = await client.post(
        f"/api/v1/resumes/{resume.json()['id']}/versions", json={"text": resume.json()["versions"][0]["text"]}
    )
    jd = await client.post(
        "/api/v1/job-profiles",
        json={"title": "Python 后端", "text": "熟悉任务幂等处理。掌握进程重启后的任务恢复。"},
    )
    jconf = await client.post(
        f"/api/v1/job-profiles/{jd.json()['id']}/versions", json={"text": jd.json()["versions"][0]["text"]}
    )
    created = await client.post(
        "/api/v1/interviews",
        json={
            "resume_version_id": confirmed.json()["id"],
            "job_profile_version_id": jconf.json()["id"],
            "mode": "STRICT",
        },
    )
    assert created.status_code == 200, created.text
    sid = created.json()["id"]
    await client.post(f"/api/v1/interviews/{sid}/start", json={"expected_state_version": 0})
    await drain_jobs(20)
    return sid


async def answer(client, sid, text, key):
    snapshot = (await client.get(f"/api/v1/interviews/{sid}")).json()
    submitted = await client.post(
        f"/api/v1/interviews/{sid}/answers",
        json={
            "turn_id": snapshot["current_question"]["turn_id"],
            "text": text,
            "expected_state_version": snapshot["state_version"],
        },
        headers={"Idempotency-Key": key},
    )
    assert submitted.status_code == 200, submitted.text


@pytest.mark.asyncio
async def test_followup_ids_and_resolved_weakness_end_to_end():
    await reset_db()
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        sid = await start_interview(client)
        await answer(client, sid, "做了队列。", "direction-1")
        await drain_jobs(20)
        async with SessionLocal() as db:
            turns = (
                (
                    await db.execute(
                        select(InterviewTurn)
                        .where(InterviewTurn.session_id == uuid.UUID(sid))
                        .order_by(InterviewTurn.turn_no)
                    )
                )
                .scalars()
                .all()
            )
            first, followup = turns
            assert first.action == "ASK"
            assert followup.action == "FOLLOW_UP"
            assert followup.parent_turn_id == first.id
            assert followup.claim_id == first.claim_id
            assert followup.probe_key == first.probe_key
            assert followup.skill_ids == first.skill_ids
            first_rubric = await db.get(RubricSnapshot, first.rubric_snapshot_id)
            followup_rubric = await db.get(RubricSnapshot, followup.rubric_snapshot_id)
            assert first_rubric.schema_version == "v2"
            assert first_rubric.criteria_json["profile"] == "tradeoff"
            assert followup_rubric.criteria_json == first_rubric.criteria_json
            assert followup_rubric.question_hash != first_rubric.question_hash
        await answer(client, sid, "我先固定测试集和生成配置，再对比基线和失败案例。", "direction-2")
        await drain_jobs(20)
        async with SessionLocal() as db:
            turns = (
                (
                    await db.execute(
                        select(InterviewTurn)
                        .where(InterviewTurn.session_id == uuid.UUID(sid))
                        .order_by(InterviewTurn.turn_no)
                    )
                )
                .scalars()
                .all()
            )
            assert turns[-1].action == "ASK"
            assert turns[-1].parent_turn_id is None
            assert turns[-1].probe_key != turns[0].probe_key
            assert turns[-1].skill_ids == ["req_02"]
        snapshot = (await client.get(f"/api/v1/interviews/{sid}")).json()
        await client.post(
            f"/api/v1/interviews/{sid}/finish", json={"expected_state_version": snapshot["state_version"]}
        )
        await drain_jobs(20)
        report = (await client.get(f"/api/v1/interviews/{sid}/reports/latest")).json()
        evaluated, unassessed = report["body"]["coverage"]
        assert evaluated["sample_count"] == 1
        assert evaluated["observation_count"] == 2
        assert evaluated["levels"] == [2]
        assert unassessed["assessment_status"] == "NOT_ASSESSED"
        assert report["body"]["next_training"] == []
        answered_turns = [turn for turn in report["turns"] if turn["status"] == "ANSWERED"]
        assert [turn["turn_no"] for turn in answered_turns] == [1, 2]
        assert [turn["action"] for turn in answered_turns] == ["ASK", "FOLLOW_UP"]
        assert answered_turns[1]["parent_turn_id"] == answered_turns[0]["turn_id"]
        assert answered_turns[0]["answer_text"] == "做了队列。"
        assert all(turn["question_text"] and turn["assessment"]["id"] for turn in answered_turns)
        assert answered_turns[1]["assessment"]["dimensions"][0]["level"] == 2
        assert len(report["questions"]) == len(report["turns"])
        assert all(turn["rubric_snapshot_id"] in report["rubrics"] for turn in report["turns"])
        assert report["training"]["eligible"] is False
        assert "暂无需要训练" in report["training"]["reason"]
        response = await client.post("/api/v1/training-plans", json={"session_id": sid})
        assert response.status_code == 403
        assert response.json()["message"] == report["training"]["reason"]


@pytest.mark.asyncio
async def test_report_waits_repeatedly_then_refreshes_after_late_evaluation(monkeypatch):
    await reset_db()
    from api.main import app

    monkeypatch.setattr(settings, "report_grace_seconds", 60)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        sid = await start_interview(client)
        await answer(client, sid, "我固定测试集和基线，用并发测试验证幂等和失败重试。", "late-eval")
        async with SessionLocal() as db:
            eval_job = (
                await db.execute(
                    select(Job).where(
                        Job.session_id == uuid.UUID(sid), Job.kind == JobKind.EVALUATE_TURN.value
                    )
                )
            ).scalar_one()
            eval_job.available_at = datetime.now(UTC) + timedelta(hours=1)
            eval_id = eval_job.id
            await db.commit()
        snapshot = (await client.get(f"/api/v1/interviews/{sid}")).json()
        finished = await client.post(
            f"/api/v1/interviews/{sid}/finish", json={"expected_state_version": snapshot["state_version"]}
        )
        assert finished.status_code == 200, finished.text
        for _ in range(2):
            async with SessionLocal() as db:
                report_job = (
                    await db.execute(
                        select(Job).where(
                            Job.session_id == uuid.UUID(sid), Job.kind == JobKind.BUILD_REPORT.value
                        )
                    )
                ).scalar_one()
                report_job.available_at = datetime.now(UTC) - timedelta(seconds=1)
                await db.commit()
            await drain_jobs(10)
            async with SessionLocal() as db:
                reports = (
                    (await db.execute(select(Report).where(Report.session_id == uuid.UUID(sid))))
                    .scalars()
                    .all()
                )
                assert reports == []
                report_job = await db.get(Job, report_job.id)
                assert report_job.status == JobStatus.QUEUED.value
                assert report_job.attempt_count == 0
        async with SessionLocal() as db:
            report_job = await db.get(Job, report_job.id)
            report_job.created_at = datetime.now(UTC) - timedelta(seconds=61)
            report_job.available_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
        await drain_jobs(10)
        partial = (await client.get(f"/api/v1/interviews/{sid}/reports/latest")).json()
        assert partial["status"] == "PARTIAL"
        assert partial["missing_turn_ids"]
        assert partial["turns"][0]["answer_text"]
        assert partial["turns"][0]["assessment"]["status"] == "PENDING"
        assert partial["turns"][0]["assessment"]["dimensions"] == []
        assert partial["training"]["eligible"] is False
        assert "仍在处理中" in partial["training"]["reason"]
        blocked = await client.post("/api/v1/training-plans", json={"session_id": sid})
        assert blocked.status_code == 403
        async with SessionLocal() as db:
            eval_job = await db.get(Job, eval_id)
            eval_job.available_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
        await drain_jobs(20)
        complete = (await client.get(f"/api/v1/interviews/{sid}/reports/latest")).json()
        assert complete["status"] == "COMPLETE"
        assert complete["revision"] > partial["revision"]
        assert complete["missing_turn_ids"] == []
