import uuid

import pytest
from sqlalchemy import select

from db.models import Assessment, Report
from db.session import SessionLocal
from tests.integration.test_answer_idempotency import reset_db
from tests.integration.test_design_regressions import answer, start_interview
from worker.runner import drain_jobs


@pytest.mark.asyncio
async def test_report_turn_uses_frozen_assessment_until_new_report_is_built(client):
    await reset_db()
    sid = await start_interview(client)
    await answer(client, sid, "我先固定测试集和生成配置，再对比基线和失败案例。", "report-snapshot")
    await drain_jobs(20)
    snapshot = (await client.get(f"/api/v1/interviews/{sid}")).json()
    await client.post(
        f"/api/v1/interviews/{sid}/finish", json={"expected_state_version": snapshot["state_version"]}
    )
    await drain_jobs(20)
    original = (await client.get(f"/api/v1/interviews/{sid}/reports/latest")).json()
    first = original["turns"][0]
    async with SessionLocal() as db:
        assessment = await db.get(Assessment, uuid.UUID(first["assessment"]["id"]))
        db.add(Assessment(
            turn_id=assessment.turn_id, session_id=assessment.session_id,
            owner_id=assessment.owner_id, revision=2, status="INSUFFICIENT_EVIDENCE",
            rubric_snapshot_id=assessment.rubric_snapshot_id,
            result_json={"dimensions": []}, model_config_id="test",
        ))
        await db.commit()
    unchanged = (await client.get(f"/api/v1/interviews/{sid}/reports/latest")).json()
    assert unchanged["turns"][0]["assessment"] == first["assessment"]
    assert unchanged["body"] == original["body"]


@pytest.mark.asyncio
async def test_failed_assessment_is_visible_per_turn_without_a_zero_score(client):
    await reset_db()
    sid = await start_interview(client)
    await answer(client, sid, "我先固定测试集和生成配置，再对比基线和失败案例。", "report-failure")
    await drain_jobs(20)
    async with SessionLocal() as db:
        assessment = (await db.execute(select(Assessment).where(
            Assessment.session_id == uuid.UUID(sid),
        ))).scalar_one()
        assessment.status = "FAILED"
        assessment.result_json = {"dimensions": []}
        await db.commit()
    snapshot = (await client.get(f"/api/v1/interviews/{sid}")).json()
    await client.post(
        f"/api/v1/interviews/{sid}/finish", json={"expected_state_version": snapshot["state_version"]}
    )
    await drain_jobs(20)
    report = (await client.get(f"/api/v1/interviews/{sid}/reports/latest")).json()
    assert report["status"] == "PARTIAL"
    first = report["turns"][0]
    assert first["question_text"] and first["answer_text"]
    assert first["assessment"]["status"] == "FAILED"
    assert first["assessment"]["dimensions"] == []
    assert first["assessment"]["id"] is None
    assert report["training"]["eligible"] is False
    # Existing training rules allow confirmed weaknesses in a partial report once jobs finish.
    async with SessionLocal() as db:
        row = (await db.execute(select(Report).where(Report.session_id == uuid.UUID(sid)))).scalar_one()
        row.body_json = {**row.body_json, "next_training": ["req_01"]}
        await db.commit()
    partial_with_weakness = (await client.get(f"/api/v1/interviews/{sid}/reports/latest")).json()
    assert partial_with_weakness["training"] == {"eligible": True, "reason": None}
