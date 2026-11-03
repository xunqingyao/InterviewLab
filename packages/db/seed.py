from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select, text as sql_text

from db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeEmbedding, KnowledgeSnapshot, KnowledgeVersion, QuestionTemplate, RubricTemplate, SkillCatalog
from db.session import SessionLocal
from retrieval import embed, tsvector_document

ROOT = Path(__file__).resolve().parents[2]
QUESTIONS = ROOT / "seed" / "questions" / "catalog.jsonl"
RUBRIC_DIR = ROOT / "seed" / "rubrics"
REFERENCES = ROOT / "seed" / "references" / "core.md"

SKILLS = [
    {"id": "project.decision", "name": "项目理解与决策"},
    {"id": "backend.idempotency", "name": "幂等与重复提交"},
    {"id": "backend.jobs", "name": "任务队列与恢复"},
    {"id": "backend.events", "name": "事件补读"},
    {"id": "backend.transactions", "name": "事务边界"},
    {"id": "backend.failure", "name": "失败语义"},
    {"id": "rag.evaluation_design", "name": "RAG 评测设计"},
    {"id": "ai.agent_state", "name": "Agent 状态"},
    {"id": "ai.tool_boundaries", "name": "工具边界"},
    {"id": "ai.multi_agent", "name": "多 Agent 取舍"},
    {"id": "ai.context", "name": "上下文冻结"},
    {"id": "eval.citation", "name": "证据引用"},
    {"id": "security.privacy", "name": "隐私边界"},
]


RUBRICS = {
    "rubric_idempotency_v1": ["backend.idempotency"],
    "rubric_jobs_v1": ["backend.jobs"],
    "rubric_rag_eval_v1": ["rag.evaluation_design"],
    "rubric_agent_state_v1": ["ai.agent_state"],
    "rubric_tools_v1": ["ai.tool_boundaries"],
    "rubric_events_v1": ["backend.events"],
    "rubric_citation_v1": ["eval.citation"],
    "rubric_tx_v1": ["backend.transactions"],
    "rubric_privacy_v1": ["security.privacy"],
    "rubric_multi_agent_v1": ["ai.multi_agent"],
    "rubric_failure_v1": ["backend.failure"],
    "rubric_cutoff_v1": ["ai.context"],
}


async def seed() -> None:
    async with SessionLocal() as db:
        existing = (await db.execute(select(SkillCatalog))).scalars().first()
        if existing is None:
            db.add(SkillCatalog(catalog_key="ai_python_backend", version="skills_v1", skills_json=SKILLS))
        if QUESTIONS.exists():
            for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                found = (
                    await db.execute(
                        select(QuestionTemplate).where(
                            QuestionTemplate.template_key == item["template_key"],
                            QuestionTemplate.version == item["version"],
                        )
                    )
                ).scalar_one_or_none()
                if found:
                    continue
                db.add(QuestionTemplate(**item, review_status="APPROVED"))
        for path in RUBRIC_DIR.glob("*.json"):
            loaded = json.loads(path.read_text(encoding="utf-8"))
            items = loaded if isinstance(loaded, list) else [loaded]
            for data in items:
                found = (
                    await db.execute(select(RubricTemplate).where(RubricTemplate.template_key == data["template_key"]))
                ).scalar_one_or_none()
                if not found:
                    db.add(
                        RubricTemplate(
                            template_key=data["template_key"],
                            version=data.get("version", "v1"),
                            skill_ids=data.get("skill_ids", []),
                            criteria_json=data.get("criteria_json", {}),
                        )
                    )
                elif data.get("criteria_json", {}).get("goal") and not (found.criteria_json or {}).get("goal"):
                    found.criteria_json = data["criteria_json"]
                    found.skill_ids = data.get("skill_ids", found.skill_ids)
        for key, skills in RUBRICS.items():
            found = (await db.execute(select(RubricTemplate).where(RubricTemplate.template_key == key))).scalar_one_or_none()
            if not found:
                db.add(
                    RubricTemplate(
                        template_key=key,
                        version="v1",
                        skill_ids=skills,
                        criteria_json={"levels": {"0": "error", "1": "partial", "2": "ok", "3": "strong"}},
                    )
                )
        if REFERENCES.exists() and (await db.execute(select(KnowledgeDocument))).scalars().first() is None:
            doc = KnowledgeDocument(source_type="self_authored", title="InterviewLab core notes", visibility="GLOBAL")
            db.add(doc)
            await db.flush()
            version = KnowledgeVersion(document_id=doc.id, version=1, content_hash="seed", review_status="APPROVED")
            db.add(version)
            await db.flush()
            chunks = []
            current = []
            heading = "general"
            for line in REFERENCES.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    if current:
                        chunks.append((heading, "\n".join(current)))
                    heading = line[3:].strip()
                    current = [line]
                else:
                    current.append(line)
            if current:
                chunks.append((heading, "\n".join(current)))
            version_ids = [str(version.id)]
            for idx, (heading, body) in enumerate(chunks):
                row = KnowledgeChunk(
                    version_id=version.id,
                    chunk_no=idx,
                    heading_path=heading,
                    text=body,
                    skill_tags=[heading] if heading != "general" else [],
                    token_count=len(body),
                )
                db.add(row)
                await db.flush()
                db.add(
                    KnowledgeEmbedding(
                        chunk_id=row.id,
                        embedding_model_id="hash-bow-v1",
                        dimension=32,
                        vector=embed(body),
                    )
                )
                await db.execute(
                    sql_text("UPDATE knowledge_chunks SET tsv = to_tsvector('simple', :tok) WHERE id = :id"),
                    {"tok": tsvector_document(body), "id": row.id},
                )
            db.add(KnowledgeSnapshot(version_ids=version_ids, summary="seed knowledge"))
        for chunk in (await db.execute(select(KnowledgeChunk))).scalars():
            await db.execute(
                sql_text("UPDATE knowledge_chunks SET tsv = to_tsvector('simple', :tok) WHERE id = :id"),
                {"tok": tsvector_document(chunk.text), "id": chunk.id},
            )
        await db.commit()


def main() -> None:
    import asyncio

    asyncio.run(seed())


if __name__ == "__main__":
    main()
