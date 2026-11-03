from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import current_user, get_db
from db.commands import confirm_job_profile, confirm_resume, create_job_profile, create_resume, delete_resume
from db.models import JobProfileVersion, Resume, ResumeVersion, User

router = APIRouter()


class ConfirmBody(BaseModel):
    text: str
    structured: dict | None = None
    requirements: dict | None = None


class JobProfileBody(BaseModel):
    title: str
    text: str


@router.post("/api/v1/resumes")
async def post_resume(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        payload = await request.json()
        resume = await create_resume(db, user.id, payload.get("text"), None, None, None)
    else:
        form = await request.form()
        upload = form.get("file")
        text = form.get("text")
        data = await upload.read() if hasattr(upload, "read") else None
        filename = getattr(upload, "filename", None)
        content_type = getattr(upload, "content_type", None)
        resume = await create_resume(db, user.id, str(text) if text else None, filename, data, content_type)
    versions = (await db.execute(select(ResumeVersion).where(ResumeVersion.resume_id == resume.id))).scalars().all()
    return {
        "id": str(resume.id),
        "status": resume.status,
        "parse_error": resume.parse_error,
        "versions": [{"id": str(v.id), "version": v.version, "text": v.normalized_text, "structured": v.structured_json, "confirmed_at": v.confirmed_at} for v in versions],
    }


@router.get("/api/v1/resumes/{resume_id}")
async def get_resume(resume_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    resume = await db.get(Resume, resume_id)
    if resume is None:
        from domain.errors import ResourceNotFound

        raise ResourceNotFound("resume not found")
    from domain.policies import assert_owner

    assert_owner(resume.owner_id, user.id)
    if resume.status == "DELETED" or resume.deleted_at is not None:
        from domain.errors import ResourceNotFound

        raise ResourceNotFound("resume not found")
    versions = (await db.execute(select(ResumeVersion).where(ResumeVersion.resume_id == resume_id))).scalars().all()
    return {
        "id": str(resume.id),
        "status": resume.status,
        "parse_error": resume.parse_error,
        "versions": [{"id": str(v.id), "version": v.version, "text": v.normalized_text, "structured": v.structured_json, "confirmed_at": v.confirmed_at} for v in versions],
    }


@router.post("/api/v1/resumes/{resume_id}/versions")
async def post_resume_version(resume_id: uuid.UUID, body: ConfirmBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    version = await confirm_resume(db, user.id, resume_id, body.text, body.structured)
    return {"id": str(version.id), "version": version.version, "confirmed_at": version.confirmed_at.isoformat()}


@router.delete("/api/v1/resumes/{resume_id}")
async def remove_resume(resume_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    await delete_resume(db, user.id, resume_id)
    return {"deleted": True}


@router.post("/api/v1/job-profiles")
async def post_job_profile(body: JobProfileBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    profile = await create_job_profile(db, user.id, body.title, body.text)
    versions = (await db.execute(select(JobProfileVersion).where(JobProfileVersion.profile_id == profile.id))).scalars().all()
    return {"id": str(profile.id), "title": profile.title, "versions": [{"id": str(v.id), "version": v.version, "text": v.text, "requirements": v.requirements_json, "confirmed_at": v.confirmed_at} for v in versions]}


@router.post("/api/v1/job-profiles/{profile_id}/versions")
async def post_job_profile_version(profile_id: uuid.UUID, body: ConfirmBody, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    version = await confirm_job_profile(db, user.id, profile_id, body.text)
    return {
        "id": str(version.id),
        "version": version.version,
        "confirmed_at": version.confirmed_at.isoformat(),
        "requirements": version.requirements_json,
    }
