from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import current_user, get_db
from db.commands import (
    cancel_interview,
    confirm_audio_draft,
    create_audio_draft,
    create_interview,
    delete_interview,
    finish_interview,
    list_events,
    regenerate_report,
    report_view,
    session_snapshot,
    skip_turn,
    start_interview,
    submit_answer,
)
from db.models import InterviewSession, User
from db.session import SessionLocal
from domain.errors import EventCursorExpired, ResourceNotFound
from domain.events import PUBLIC_SSE_EVENTS
from domain.schemas import SubmitAnswerCommand

router = APIRouter()


class CreateInterviewBody(BaseModel):
    resume_version_id: uuid.UUID
    job_profile_version_id: uuid.UUID
    mode: str = "STRICT"
    duration_minutes: int = 15


class VersionBody(BaseModel):
    expected_state_version: int
    turn_id: uuid.UUID | None = None
    reason: str | None = None
    text: str | None = None


class AudioConfirmBody(BaseModel):
    text: str


@router.post("/api/v1/interviews")
async def post_interview(body: CreateInterviewBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    session = await create_interview(db, user.id, body.resume_version_id, body.job_profile_version_id, body.mode, body.duration_minutes)
    return {"id": str(session.id), "status": session.status, "state_version": session.state_version}


@router.get("/api/v1/interviews")
async def list_interviews(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.execute(select(InterviewSession).where(InterviewSession.owner_id == user.id).order_by(InterviewSession.created_at.desc()))).scalars().all()
    return {
        "items": [
            {"id": str(s.id), "status": s.status, "phase": s.phase, "mode": s.mode, "state_version": s.state_version, "created_at": s.created_at.isoformat()}
            for s in rows
        ]
    }


@router.get("/api/v1/interviews/{session_id}")
async def get_interview(session_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await session_snapshot(db, user.id, session_id)


@router.post("/api/v1/interviews/{session_id}/start")
async def post_start(session_id: uuid.UUID, body: VersionBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await start_interview(db, user.id, session_id, body.expected_state_version)


@router.post("/api/v1/interviews/{session_id}/answers")
async def post_answer(
    session_id: uuid.UUID,
    body: SubmitAnswerCommand,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    return await submit_answer(db, user.id, session_id, body, idempotency_key)


@router.post("/api/v1/interviews/{session_id}/skip")
async def post_skip(session_id: uuid.UUID, body: VersionBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await skip_turn(db, user.id, session_id, body.turn_id, body.expected_state_version, body.reason or "skipped")


@router.post("/api/v1/interviews/{session_id}/finish")
async def post_finish(session_id: uuid.UUID, body: VersionBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await finish_interview(db, user.id, session_id, body.expected_state_version)


@router.post("/api/v1/interviews/{session_id}/cancel")
async def post_cancel(session_id: uuid.UUID, body: VersionBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await cancel_interview(db, user.id, session_id, body.expected_state_version)


@router.delete("/api/v1/interviews/{session_id}")
async def remove_interview(session_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await delete_interview(db, user.id, session_id)
    return {"deleted": True}


@router.get("/api/v1/interviews/{session_id}/transcript")
async def get_transcript(session_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    snap = await session_snapshot(db, user.id, session_id)
    return {"messages": snap["messages"]}


@router.get("/api/v1/interviews/{session_id}/reports/latest")
async def get_report(session_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return await report_view(db, user.id, session_id)


@router.post("/api/v1/interviews/{session_id}/reports")
async def post_report(session_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    job_id = await regenerate_report(db, user.id, session_id)
    return {"job_id": str(job_id) if job_id else None}


@router.post("/api/v1/interviews/{session_id}/audio-drafts")
async def post_audio(session_id: uuid.UUID, turn_id: uuid.UUID = Header(default=None, alias="X-Turn-Id"), file: UploadFile | None = None, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    # also accept query
    return {"error": "use multipart turn_id"}


@router.post("/api/v1/interviews/{session_id}/audio-drafts/{turn_id}")
async def post_audio_turn(session_id: uuid.UUID, turn_id: uuid.UUID, file: UploadFile, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    data = await file.read()
    draft = await create_audio_draft(db, user.id, session_id, turn_id, data, file.filename or "audio.webm")
    return {"id": str(draft.id), "status": draft.status}


@router.post("/api/v1/audio-drafts/{draft_id}/confirm")
async def post_audio_confirm(draft_id: uuid.UUID, body: AudioConfirmBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    draft = await confirm_audio_draft(db, user.id, draft_id, body.text)
    return {"id": str(draft.id), "status": draft.status, "text": draft.transcript_edited}


@router.get("/api/v1/interviews/{session_id}/events")
async def get_events(
    session_id: uuid.UUID,
    request: Request,
    cursor: int = Query(default=0),
    once: bool = Query(default=False),
    user: User = Depends(current_user),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    start = int(last_event_id) if last_event_id and last_event_id.isdigit() else cursor
    async with SessionLocal() as db:
        await session_snapshot(db, user.id, session_id)

    async def gen():
        current = start
        yield ": heartbeat\n\n"
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            try:
                async with SessionLocal() as db:
                    events = await list_events(db, user.id, session_id, current)
            except EventCursorExpired:
                payload = json.dumps({"session_id": str(session_id), "reason": "EVENT_CURSOR_EXPIRED"})
                yield f"event: resync.required\ndata: {payload}\n\n"
                break
            except ResourceNotFound:
                payload = json.dumps({"session_id": str(session_id), "reason": "NOT_FOUND"})
                yield f"event: resync.required\ndata: {payload}\n\n"
                break
            if events:
                idle = 0
                for event in events:
                    if event.type not in PUBLIC_SSE_EVENTS:
                        continue
                    current = event.seq
                    yield f"id: {event.seq}\nevent: {event.type}\ndata: {json.dumps(event.public_payload, ensure_ascii=False)}\n\n"
                if once:
                    break
            else:
                idle += 1
                yield ": heartbeat\n\n"
                if once:
                    break
                await asyncio.sleep(1 if idle < 5 else 3)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})
