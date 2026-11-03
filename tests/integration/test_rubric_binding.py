import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from db.models import Assessment, InterviewTurn, Job, RubricSnapshot
from db.session import SessionLocal
from domain.schemas import AssessmentResult, QuestionProposal
from tests.integration.test_answer_idempotency import reset_db
from tests.integration.test_design_regressions import answer, start_interview
from worker.runner import drain_jobs


@pytest.mark.asyncio
async def test_wrong_assessment_ids_cannot_reach_database(monkeypatch):
    await reset_db()
    from api.main import app
    from db import handlers

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        sid = await start_interview(client)
        await answer(client, sid, "我用数据库唯一约束防止重复写入。", "binding-reject")
        async with SessionLocal() as db:
            eval_job = (
                await db.execute(
                    select(Job).where(Job.session_id == uuid.UUID(sid), Job.kind == "EVALUATE_TURN")
                )
            ).scalar_one()
            eval_job.priority = 500
            await db.commit()

        async def incorrect_evaluator(snapshot, **kwargs):
            return AssessmentResult(
                turn_id=str(uuid.uuid4()),
                rubric_snapshot_id=snapshot["rubric_snapshot_id"],
                status="ASSESSED",
                dimensions=[],
            )

        monkeypatch.setattr(handlers, "evaluate_turn", incorrect_evaluator)
        assert await drain_jobs(1) == 1
        async with SessionLocal() as db:
            assert (await db.execute(select(func.count()).select_from(Assessment))).scalar() == 0
            job = await db.get(Job, eval_job.id)
            assert job.status == "RETRY_WAIT"
            assert job.last_error_code == "InvalidAgentOutput"


@pytest.mark.asyncio
async def test_persistence_layer_rejects_question_bypassing_agent_validation(monkeypatch):
    await reset_db()
    from api.main import app
    from db import handlers

    async def incorrect_interviewer(snapshot, **kwargs):
        return QuestionProposal(
            action="ASK",
            claim_id="not_in_resume",
            probe_key="not_in_resume:0",
            topic_id="req_01",
            skill_ids=["req_01"],
            question_text="未经授权的问题",
            rubric_template_id="rubric_question_v2",
            decision_summary="不可信提案",
        )

    monkeypatch.setattr(handlers, "propose_next_question", incorrect_interviewer)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        sid = await start_interview(client)
        async with SessionLocal() as db:
            turn = (
                await db.execute(select(InterviewTurn).where(InterviewTurn.session_id == uuid.UUID(sid)))
            ).scalar_one()
            assert turn.claim_id != "not_in_resume"
            assert turn.generation_mode == "FALLBACK_TEMPLATE"
            rubric = await db.get(RubricSnapshot, turn.rubric_snapshot_id)
            assert rubric.criteria_json["scope"]["claim_id"] == turn.claim_id
            assert rubric.criteria_json["scope"]["probe_key"] == turn.probe_key


@pytest.mark.asyncio
async def test_wrong_frozen_question_hash_blocks_evaluation(monkeypatch):
    await reset_db()
    from api.main import app
    from db import handlers

    async def must_not_call_model(*args, **kwargs):
        raise AssertionError("invalid binding must be rejected before calling the evaluator")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        sid = await start_interview(client)
        await answer(client, sid, "我用数据库唯一约束防止重复写入。", "rubric-hash-reject")
        async with SessionLocal() as db:
            turn = (
                await db.execute(select(InterviewTurn).where(InterviewTurn.session_id == uuid.UUID(sid)))
            ).scalar_one()
            rubric = await db.get(RubricSnapshot, turn.rubric_snapshot_id)
            rubric.question_hash = "not_this_question"
            eval_job = (
                await db.execute(
                    select(Job).where(Job.session_id == uuid.UUID(sid), Job.kind == "EVALUATE_TURN")
                )
            ).scalar_one()
            eval_job.priority = 500
            await db.commit()
        monkeypatch.setattr(handlers, "evaluate_turn", must_not_call_model)
        assert await drain_jobs(1) == 1
        async with SessionLocal() as db:
            job = await db.get(Job, eval_job.id)
            assert job.status == "RETRY_WAIT"
            assert job.last_error_code == "InvalidAgentOutput"
            assert (await db.execute(select(func.count()).select_from(Assessment))).scalar() == 0


@pytest.mark.parametrize("invalid_field", ["dimension", "gap"])
@pytest.mark.asyncio
async def test_out_of_scope_evaluation_retries_then_fails_without_stopping_worker(monkeypatch, invalid_field):
    await reset_db()
    from db import handlers

    async def incorrect_evaluator(snapshot, **kwargs):
        return AssessmentResult(
            turn_id=snapshot["turn_id"],
            rubric_snapshot_id=snapshot["rubric_snapshot_id"],
            status="ASSESSED",
            dimensions=[
                {
                    "skill_id": "rubric_check_not_requirement" if invalid_field == "dimension" else "req_01",
                    "level": 2,
                    "evidence_status": "DEMONSTRATED",
                    "answer_evidence": [
                        {"message_id": snapshot["answer_message_id"], "quote": snapshot["answer_text"]}
                    ],
                }
            ],
            verification_gaps=[
                {"skill_id": "other_requirement", "description": "不属于本题的缺口"}
            ] if invalid_field == "gap" else [],
        )

    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        sid = await start_interview(client)
        await answer(client, sid, "我用数据库唯一约束防止重复写入。", f"reject-{invalid_field}")
        async with SessionLocal() as db:
            eval_job = (
                await db.execute(
                    select(Job).where(Job.session_id == uuid.UUID(sid), Job.kind == "EVALUATE_TURN")
                )
            ).scalar_one()
            eval_job.priority = 500
            await db.commit()
        monkeypatch.setattr(handlers, "evaluate_turn", incorrect_evaluator)
        for attempt in range(1, 4):
            assert await drain_jobs(1) == 1
            async with SessionLocal() as db:
                job = await db.get(Job, eval_job.id)
                assert job.attempt_count == attempt
                assert job.last_error_code == "InvalidAgentOutput"
                assert job.status == ("RETRY_WAIT" if attempt < 3 else "DEAD")
                if attempt < 3:
                    assert (await db.execute(select(func.count()).select_from(Assessment))).scalar() == 0
                else:
                    failed = (await db.execute(select(Assessment))).scalar_one()
                    assert failed.status == "FAILED"
                    assert failed.result_json["dimensions"] == []
        # Other jobs still run: generation of the next question does not stop with scoring.
        assert await drain_jobs(1) == 1
        snapshot = (await client.get(f"/api/v1/interviews/{sid}")).json()
        assert snapshot["current_question"]["turn_no"] == 2
