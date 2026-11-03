from __future__ import annotations

from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from pypdf import PdfWriter
from sqlalchemy import select

from db.models import KnowledgeChunk, KnowledgeEmbedding, KnowledgeVersion
from db.session import SessionLocal
from retrieval import embed
from tests.integration.test_answer_idempotency import reset_db
from worker.runner import drain_jobs


def _blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_blank_pdf_falls_back_to_paste():
    await reset_db()
    from api.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        files = {"file": ("resume.pdf", _blank_pdf(), "application/pdf")}
        created = await client.post("/api/v1/resumes", files=files)
        assert created.status_code == 200, created.text
        assert created.json()["status"] == "PENDING"
        await drain_jobs(10)
        row = await client.get(f"/api/v1/resumes/{created.json()['id']}")
        body = row.json()
        assert body["status"] == "NEEDS_TEXT_INPUT"
        pasted = await client.post(
            f"/api/v1/resumes/{created.json()['id']}/versions",
            json={"text": "梁开新，粘贴的简历文本。主导过 FastAPI 任务队列。"},
        )
        assert pasted.status_code == 200


@pytest.mark.asyncio
async def test_search_stays_inside_session_snapshot():
    await reset_db()
    from api.main import app
    from db.commands import search_reference
    from db.models import InterviewSession
    import uuid

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000") as client:
        resume = await client.post("/api/v1/resumes", json={"text": "做过 RAG 评测，会固定测试集。"})
        confirmed = await client.post(
            f"/api/v1/resumes/{resume.json()['id']}/versions",
            json={"text": resume.json()["versions"][0]["text"]},
        )
        jd = await client.post("/api/v1/job-profiles", json={"title": "后端", "text": "必须熟悉评测。"})
        jconf = await client.post(
            f"/api/v1/job-profiles/{jd.json()['id']}/versions",
            json={"text": jd.json()["versions"][0]["text"]},
        )
        session = await client.post(
            "/api/v1/interviews",
            json={"resume_version_id": confirmed.json()["id"], "job_profile_version_id": jconf.json()["id"]},
        )
        sid = uuid.UUID(session.json()["id"])

    outsider_id = None
    async with SessionLocal() as db:
        row = await db.get(InterviewSession, sid)
        existing = (await db.execute(select(KnowledgeVersion))).scalars().first()
        other = KnowledgeVersion(document_id=existing.document_id, version=99, content_hash="outsider")
        db.add(other)
        await db.flush()
        chunk = KnowledgeChunk(
            version_id=other.id,
            chunk_no=99,
            heading_path="outsider",
            text="这段话绝对不该被本场检索到：秘密参考答案 999",
            skill_tags=["rag.evaluation_design"],
            token_count=20,
        )
        db.add(chunk)
        await db.flush()
        db.add(
            KnowledgeEmbedding(
                chunk_id=chunk.id,
                embedding_model_id="hash-bow-v1",
                dimension=32,
                vector=embed(chunk.text),
            )
        )
        outsider_id = str(chunk.id)
        await db.commit()
        hits = await search_reference(db, row, "固定测试集 检索", "rag.evaluation_design")
        hit_ids = {h["id"] for h in hits}
        assert outsider_id not in hit_ids
        assert all("秘密参考答案 999" not in (h.get("text") or "") for h in hits)
