import uuid

import pytest
from sqlalchemy import select

from agents.context import build_coach_context, build_interviewer_context
from db.handlers import _build_coach_snapshot, _build_interviewer_snapshot
from db.models import InterviewSession, InterviewTurn, Job, ResumeVersion
from db.session import SessionLocal
from domain.enums import JobKind
from tests.integration.test_answer_idempotency import reset_db
from tests.integration.test_design_regressions import start_interview


@pytest.mark.asyncio
async def test_confirmed_resume_claims_reach_question_and_agent_contexts(client):
    await reset_db()
    sid = await start_interview(client)
    response = await client.get(f"/api/v1/interviews/{sid}")
    assert response.status_code == 200, response.text
    question = response.json()["current_question"]["text"]

    async with SessionLocal() as db:
        session = await db.get(InterviewSession, uuid.UUID(sid))
        resume = await db.get(ResumeVersion, session.resume_version_id)
        frozen = resume.structured_json["claims"]
        assert frozen["source"] == "model"
        assert frozen["items"]
        expected_texts = [row["text"] for row in frozen["items"]]
        assert all(text in resume.normalized_text for text in expected_texts)
        assert any(text in question for text in expected_texts)
        assert "items" not in question
        assert "source" not in question

        job = (
            await db.execute(
                select(Job).where(
                    Job.session_id == session.id,
                    Job.kind == JobKind.GENERATE_QUESTION.value,
                )
            )
        ).scalar_one()
        turns = (
            (
                await db.execute(
                    select(InterviewTurn)
                    .where(InterviewTurn.session_id == session.id)
                    .order_by(InterviewTurn.turn_no)
                )
            )
            .scalars()
            .all()
        )
        interviewer = await _build_interviewer_snapshot(db, session, job, turns)
        coach = await _build_coach_snapshot(db, session, job)
        assert interviewer["claims"] == frozen
        assert coach["claims"] == frozen

        # Check the model-facing packs, not just the database envelope.
        interviewer_pack = build_interviewer_context(interviewer)
        current = interviewer_pack["claims"]["current"]
        assert current["id"] == turns[-1].claim_id
        assert current["text"] in expected_texts
        assert [row["text"] for row in interviewer_pack["claims"]["unused"]] == expected_texts
        coach_pack = build_coach_context(coach)
        assert [row["text"] for row in coach_pack["claims"]] == expected_texts[:8]
