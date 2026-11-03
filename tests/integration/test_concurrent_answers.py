import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from db.models import Message
from db.session import SessionLocal
from tests.integration.test_answer_idempotency import reset_db
from tests.integration.test_design_regressions import start_interview


@pytest.mark.asyncio
@pytest.mark.parametrize("same_key", [True, False], ids=["same-idempotency-key", "different-keys"])
async def test_concurrent_answer_submissions_have_no_duplicate_or_unhandled_error(client, same_key):
    await reset_db()
    session_id = await start_interview(client)
    snapshot = (await client.get(f"/api/v1/interviews/{session_id}")).json()
    turn_id = snapshot["current_question"]["turn_id"]
    payload = {
        "turn_id": turn_id,
        "text": "按角色隔离上下文并校验模型提案。",
        "expected_state_version": snapshot["state_version"],
    }
    first, second = await asyncio.gather(
        client.post(
            f"/api/v1/interviews/{session_id}/answers",
            json=payload,
            headers={"Idempotency-Key": "concurrent-a"},
        ),
        client.post(
            f"/api/v1/interviews/{session_id}/answers",
            json=payload,
            headers={"Idempotency-Key": "concurrent-a" if same_key else "concurrent-b"},
        ),
    )
    if same_key:
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
    else:
        assert sorted([first.status_code, second.status_code]) == [200, 409]
    async with SessionLocal() as db:
        count = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.turn_id == uuid.UUID(turn_id), Message.kind == "ANSWER")
            )
        ).scalar_one()
        assert count == 1
