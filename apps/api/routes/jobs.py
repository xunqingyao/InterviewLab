from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import current_user, get_db
from db.models import Job, User
from domain.errors import ResourceNotFound
from domain.policies import assert_owner

router = APIRouter()


@router.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    job = await db.get(Job, job_id)
    if job is None:
        raise ResourceNotFound("job not found")
    assert_owner(job.owner_id, user.id)
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "attempt_count": job.attempt_count,
        "last_error_code": job.last_error_code,
        "retryable": job.status in {"RETRY_WAIT", "DEAD"} and job.attempt_count < job.max_attempts,
    }


@router.post("/api/v1/jobs/{job_id}/retry")
async def retry_job(job_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    job = await db.get(Job, job_id)
    if job is None:
        raise ResourceNotFound("job not found")
    assert_owner(job.owner_id, user.id)
    if job.status not in {"DEAD", "RETRY_WAIT"}:
        return {"ok": False, "status": job.status}
    job.status = "QUEUED"
    job.last_error_code = None
    return {"ok": True, "status": job.status}
