from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import current_user, get_db
from db.commands import get_assessment, request_review
from db.models import User

router = APIRouter()


class ReviewBody(BaseModel):
    reason: str


@router.get("/api/v1/assessments/{assessment_id}")
async def get_one(assessment_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    row = await get_assessment(db, user.id, assessment_id)
    return {
        "id": str(row.id),
        "turn_id": str(row.turn_id),
        "revision": row.revision,
        "status": row.status,
        "result": row.result_json,
        "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None,
        "rubric_snapshot_id": str(row.rubric_snapshot_id),
    }


@router.post("/api/v1/assessments/{assessment_id}/reviews")
async def post_review(assessment_id: uuid.UUID, body: ReviewBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    req = await request_review(db, user.id, assessment_id, body.reason)
    return {"id": str(req.id), "status": req.status}
