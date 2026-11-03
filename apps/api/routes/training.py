from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import current_user, get_db
from db.commands import create_training_plan, owned_session, serialize_training_plans, submit_exercise
from db.models import Job, User

router = APIRouter()


class PlanBody(BaseModel):
    session_id: uuid.UUID


class ExerciseBody(BaseModel):
    text: str
    hint_level: str = "NONE"


@router.post("/api/v1/training-plans")
async def post_plan(body: PlanBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    job_id = await create_training_plan(db, user.id, body.session_id)
    return {"job_id": str(job_id) if job_id else None}


@router.get("/api/v1/training-plans")
async def list_plans(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    return {"items": await serialize_training_plans(db, user.id)}


@router.get("/api/v1/training-plans/job")
async def training_job(
    session_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)
):
    await owned_session(db, session_id, user.id)
    job = (
        await db.execute(
            select(Job)
            .where(
                Job.session_id == session_id,
                Job.owner_id == user.id,
                Job.kind == "BUILD_TRAINING_PLAN",
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job is None:
        return {"job": None}
    return {
        "job": {
            "id": str(job.id),
            "status": job.status,
            "attempt_count": job.attempt_count,
            "max_attempts": job.max_attempts,
            "last_error_code": job.last_error_code,
            "error_reason": (job.result_ref or {}).get("error_reason"),
            "no_confirmed_weakness": (job.result_ref or {}).get("no_confirmed_weakness", False),
        }
    }


@router.post("/api/v1/exercises/{item_id}/submissions")
async def post_exercise(
    item_id: uuid.UUID,
    body: ExerciseBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    row = await submit_exercise(db, user.id, item_id, body.text, body.hint_level)
    return {"id": str(row.id), "hint_level": row.hint_level, "independent": row.hint_level == "NONE"}
