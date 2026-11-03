from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tests.integration.test_answer_idempotency import reset_db
from worker.runner import drain_jobs


@pytest.mark.asyncio
async def test_sse_replay_from_cursor():
    await reset_db()
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        resume = await client.post("/api/v1/resumes", json={"text": "做过事件补读和 SSE。"})
        rid = resume.json()["id"]
        confirmed = await client.post(f"/api/v1/resumes/{rid}/versions", json={"text": resume.json()["versions"][0]["text"]})
        jd = await client.post("/api/v1/job-profiles", json={"title": "后端", "text": "必须熟悉 SSE 与事件表。"})
        jconf = await client.post(f"/api/v1/job-profiles/{jd.json()['id']}/versions", json={"text": jd.json()["versions"][0]["text"]})
        session = await client.post(
            "/api/v1/interviews",
            json={"resume_version_id": confirmed.json()["id"], "job_profile_version_id": jconf.json()["id"]},
        )
        sid = session.json()["id"]
        await client.post(f"/api/v1/interviews/{sid}/start", json={"expected_state_version": 0})
        await drain_jobs(20)
        snap = (await client.get(f"/api/v1/interviews/{sid}")).json()
        payload = {
            "turn_id": snap["current_question"]["turn_id"],
            "text": "事件表按 seq 补读，客户端按 id 去重。",
            "expected_state_version": snap["state_version"],
        }
        await client.post(f"/api/v1/interviews/{sid}/answers", json=payload, headers={"Idempotency-Key": "sse-1"})
        await drain_jobs(20)
        from db.commands import list_events
        from db.session import SessionLocal
        from db.commands import get_or_create_dev_user
        import uuid

        async with SessionLocal() as db:
            user = await get_or_create_dev_user(db)
            events = await list_events(db, user.id, uuid.UUID(sid), 0)
            assert len(events) >= 3
            types = [e.type for e in events]
            assert "session.started" in types
            replay = await list_events(db, user.id, uuid.UUID(sid), 2)
            assert all(e.seq > 2 for e in replay)
            assert len(replay) == len([e for e in events if e.seq > 2])
            from db.settings import settings
            from domain.errors import EventCursorExpired

            old = settings.event_retention
            settings.event_retention = 1
            try:
                with pytest.raises(EventCursorExpired):
                    await list_events(db, user.id, uuid.UUID(sid), 0)
            finally:
                settings.event_retention = old

        async with client.stream("GET", f"/api/v1/interviews/{sid}/events?cursor=2&once=true") as resp:
            assert resp.headers["content-type"].startswith("text/event-stream")
            buf = "".join([chunk async for chunk in resp.aiter_text()])
            assert "id:" in buf
            assert "training.ready" not in buf