from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from agents.personalize import compose_personalized_question, next_claim
from domain.claims import claim_by_id, claim_items, extract_claims, probe_from_key
from domain.enums import EvidenceStatus, HintLevel, InterviewerAction
from domain.policies import looks_like_injection
from domain.requirements import (
    RUBRIC_EVIDENCE_ID,
    extract_requirements,
    pick_requirement,
    requirement_by_id,
    unused_requirements,
)
from domain.schemas import (
    AssessmentResult,
    ClaimsExtraction,
    CoachProposal,
    DimensionAssessment,
    QuestionDecision,
    QuestionProposal,
    RequirementsExtraction,
    ResumeClaim,
    TextSpan,
    TrainingTarget,
    VerificationGap,
)
from domain.text import normalize_text
from domain.training import relevant_claim
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext, GatewayResult, ToolSpec

VAGUE = ("优化了", "更好", "很快", "提升了效果", "感觉", "大概", "很多")
STRONG = ("测试集", "基线", "幂等", "租约", "fencing", "checkpoint", "rubric", "召回", "对照", "失败")


class FakeGateway:
    async def generate_structured(
        self,
        *,
        role: str,
        input_snapshot: AgentInputSnapshot,
        output_schema: type[BaseModel],
        tools: Sequence[ToolSpec],
        budget: BudgetContext,
    ) -> GatewayResult:
        from agents.context import (
            PACK_COACH,
            PACK_EVALUATOR,
            PACK_INTERVIEWER,
            flatten_coach_pack,
            flatten_evaluator_pack,
            flatten_interviewer_pack,
        )

        payload = input_snapshot.payload
        if payload.get("pack") == PACK_INTERVIEWER:
            payload = flatten_interviewer_pack(payload)
        elif payload.get("pack") == PACK_EVALUATOR:
            payload = flatten_evaluator_pack(payload)
        elif payload.get("pack") == PACK_COACH:
            payload = flatten_coach_pack(payload)
        if role == AgentRole.INTERVIEWER:
            data = self._interview(payload)
            if output_schema is QuestionDecision:
                action = data["action"].value if hasattr(data["action"], "value") else data["action"]
                draft = {k: data[k] for k in ("reason", "decision_summary")}
                draft["action"] = action
                if action != "END":
                    draft["question_text"] = data["question_text"]
                if action == "ASK":
                    draft.update(
                        claim_id=data["claim_id"],
                        probe_key=data["probe_key"],
                        requirement_id=data["skill_ids"][0],
                    )
                data = draft
            model = output_schema.model_validate(data)
            return GatewayResult(
                data=model.model_dump(), model="fake-interviewer-v1", prompt_version="interviewer_v3"
            )
        if role == AgentRole.EVALUATOR:
            data = self._evaluate(payload)
            model = output_schema.model_validate(data)
            return GatewayResult(
                data=model.model_dump(), model="fake-evaluator-v1", prompt_version="evaluator_v3"
            )
        if role == AgentRole.COACH:
            data = self._coach(payload)
            model = output_schema.model_validate(data)
            return GatewayResult(data=model.model_dump(), model="fake-coach-v1", prompt_version="coach_v1")
        if role == AgentRole.EXTRACTOR:
            text = payload.get("text", "")
            if payload.get("task") == "extract_requirements":
                rows = [
                    {"text": row["text"], "priority": row["priority"]}
                    for row in extract_requirements(text)["items"]
                ]
                return GatewayResult(
                    data=RequirementsExtraction(requirements=rows).model_dump(),
                    model="fake-extractor-v1",
                    prompt_version="jd_extractor_v1",
                )
            claims = [
                ResumeClaim.model_validate(item).model_dump()
                for item in (extract_claims(text).get("items") or [])
            ]
            return GatewayResult(
                data=ClaimsExtraction(claims=claims).model_dump(),
                model="fake-extractor-v1",
                prompt_version="extractor_v1",
            )
        if role == AgentRole.TRANSCRIBER:
            return GatewayResult(
                data={
                    "transcript": payload.get("hint")
                    or "（转写占位）我会先固定测试集和生成配置，再比较两种检索方案。"
                },
                model="fake-asr-v1",
                prompt_version="asr_v1",
            )
        raise ValueError(f"unknown role {role}")

    def _interview(self, payload: dict[str, Any]) -> dict[str, Any]:
        tools = payload.get("tool_results") or {}
        agenda = tools.get("get_agenda_status") or {}
        evidence = tools.get("get_turn_evidence") or {}
        answer = payload.get("last_answer") or evidence.get("answer_text") or ""
        gaps = payload.get("gaps") or []
        turn_count = int(agenda.get("answered_count") or payload.get("answered_count") or 0)
        remaining = agenda.get("remaining_question_budget", payload.get("remaining_question_budget", 8))
        remaining_seconds = payload.get("remaining_seconds")
        followup_depth = int(evidence.get("followup_depth") or payload.get("followup_depth") or 0)
        jd = payload.get("jd_plain") or ""
        unused = unused_requirements(payload)
        next_req = pick_requirement(unused) or _synthetic_requirement(payload)
        current_id = (payload.get("last_skill_ids") or evidence.get("skill_ids") or [None])[0]
        current = requirement_by_id(payload, current_id) or (
            {"id": current_id, "text": jd or "该岗位的核心要求"} if current_id else None
        )
        parent = payload.get("last_turn_id") or evidence.get("turn_id")
        if remaining_seconds is not None and int(remaining_seconds) <= 0:
            return QuestionProposal(
                action=InterviewerAction.END, decision_summary="Soft deadline reached."
            ).model_dump()
        if remaining <= 0 or turn_count >= int(
            payload.get("max_questions") or agenda.get("max_questions") or 8
        ):
            return QuestionProposal(
                action=InterviewerAction.END, decision_summary="Question or time budget reached."
            ).model_dump()
        if looks_like_injection(answer):
            base = current or next_req
            return _requirement_proposal(
                payload,
                base,
                action=InterviewerAction.FOLLOW_UP,
                parent=parent,
                answer="",
                summary="Answer looked like an instruction; treat as data and clarify the technical topic.",
            )
        if answer and any(token in answer for token in VAGUE) and turn_count and followup_depth < 2:
            return _requirement_proposal(
                payload,
                current or next_req,
                action=InterviewerAction.FOLLOW_UP,
                parent=parent,
                answer=answer,
                gaps=gaps,
                summary="核实可检验的量化陈述。",
            )
        if answer and len(answer.strip()) < 24 and turn_count and followup_depth < 2:
            return _requirement_proposal(
                payload,
                current or next_req,
                action=InterviewerAction.FOLLOW_UP,
                parent=parent,
                answer=answer,
                summary="回答过短或有歧义，先澄清再取证。",
            )
        if next_req:
            action = InterviewerAction.ASK
            return _requirement_proposal(
                payload,
                next_req,
                action=action,
                answer=answer,
                summary="Cover an uncovered job requirement using this resume.",
            )
        return QuestionProposal(
            action=InterviewerAction.END, decision_summary="Agenda complete."
        ).model_dump()

    def _evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        answer = normalize_text(payload.get("answer_text") or "")
        turn_id = payload["turn_id"]
        rubric_id = payload["rubric_snapshot_id"]
        skill_ids = payload.get("skill_ids") or ["project.decision"]
        if looks_like_injection(answer):
            return AssessmentResult(
                turn_id=turn_id,
                rubric_snapshot_id=rubric_id,
                status="INSUFFICIENT_EVIDENCE",
                dimensions=[
                    DimensionAssessment(
                        skill_id=skill_ids[0],
                        level=None,
                        evidence_status=EvidenceStatus.INSUFFICIENT_EVIDENCE,
                        answer_evidence=[],
                        demonstrated="回答被当作不可信数据，未采用其中的指令。",
                        missing="需要与题目相关的设计、约束和验证说明。",
                        hint_level=HintLevel.NONE,
                    )
                ],
                verification_gaps=[],
            ).model_dump()
        sources = payload.get("evidence_answers") or [
            {"message_id": payload["answer_message_id"], "text": answer}
        ]
        combined = "\n".join(row.get("text") or "" for row in sources)
        strong = sum(1 for token in STRONG if token in combined)
        vague = any(token in combined for token in VAGUE)
        if not combined.strip():
            level, status = None, EvidenceStatus.INSUFFICIENT_EVIDENCE
            demonstrated, missing = "无有效回答。", "需要具体设计、约束和验证方式。"
        elif any(token in answer for token in ("还没", "没有", "未说明")) or (
            any(token in combined for token in ("还没", "没有", "未说明"))
            and sum(token in answer for token in STRONG) < 2
        ):
            level, status = 1, EvidenceStatus.PARTIAL
            demonstrated, missing = "展示了部分思路。", "回答仍包含尚未完成或未说明的验证环节。"
        elif strong >= 2:
            level, status = 2, EvidenceStatus.DEMONSTRATED
            demonstrated, missing = "回答包含可检验的工程约束。", ""
        elif strong == 1 or not vague:
            level, status = 1, EvidenceStatus.PARTIAL
            demonstrated, missing = "展示了部分思路。", "缺少基线、对照实验或失败边界。"
        else:
            level, status = 1, EvidenceStatus.PARTIAL
            demonstrated, missing = "有优化意识。", "尚未说明固定测试集、可比较条件与判定方法。"
        evidence = [
            TextSpan(message_id=row["message_id"], quote=_first_quote(row["text"]))
            for row in sources
            if row.get("text", "").strip() and row.get("message_id")
        ]
        if payload.get("inject_bad_quote"):
            evidence.append(TextSpan(message_id="not-the-answer", quote="这段话并不存在于答案里"))
        gaps = []
        if status is not EvidenceStatus.DEMONSTRATED:
            gaps = [
                VerificationGap(
                    skill_id=skill_ids[0],
                    description="核实前后版本是否使用相同测试集和生成配置。",
                    priority="HIGH",
                )
            ]
        result = AssessmentResult(
            turn_id=turn_id,
            rubric_snapshot_id=rubric_id,
            status="ASSESSED" if level is not None else "INSUFFICIENT_EVIDENCE",
            dimensions=[
                DimensionAssessment(
                    skill_id=skill_id,
                    level=level,
                    evidence_status=status,
                    answer_evidence=evidence,
                    demonstrated=demonstrated,
                    missing=missing,
                    hint_level=HintLevel(payload.get("hint_level") or "NONE"),
                )
                for skill_id in skill_ids[:3]
            ],
            verification_gaps=gaps,
        )
        return result.model_dump()

    def _coach(self, payload: dict[str, Any]) -> dict[str, Any]:
        weak = payload.get("weak_requirements") or []
        targets = []
        for item in weak[:3]:
            claim = relevant_claim(item.get("text") or "", claim_items(payload))
            text = item.get("text") or item["id"]
            targets.append(
                TrainingTarget(
                    requirement_id=item["id"],
                    claim_id=claim["id"] if claim else None,
                    exercise_hint=f"解释{text}的实现过程和关键约束",
                    retest_hint=f"分析{text}的一次失败场景及验证办法",
                )
            )
        return CoachProposal(
            targets=targets,
            rationale="围绕证据不足或尚未独立掌握的岗位要求安排最小训练单元。",
        ).model_dump()


class ReplayGateway(FakeGateway):
    def __init__(self, fixtures: dict[str, dict[str, Any]] | None = None) -> None:
        self.fixtures = fixtures or {}

    async def generate_structured(self, **kwargs):  # type: ignore[no-untyped-def]
        key = json.dumps(kwargs["input_snapshot"].payload, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(key.encode()).hexdigest()
        if digest in self.fixtures:
            data = self.fixtures[digest]
            model = kwargs["output_schema"].model_validate(data)
            return GatewayResult(data=model.model_dump(), model="replay", prompt_version="replay_v1")
        return await super().generate_structured(**kwargs)


def _first_quote(answer: str) -> str:
    text = answer.strip()
    if not text:
        return ""
    sentence = re.split(r"[。.!?\n]", text, maxsplit=1)[0].strip()
    quote = sentence[:80] or text[:80]
    return quote


def _synthetic_requirement(payload: dict[str, Any]) -> dict[str, Any]:
    text = (payload.get("jd_plain") or "").strip() or "该岗位的核心要求"
    return {
        "id": "req_01",
        "priority": "must",
        "text": text[:120],
        "probe_angle": "说明约束、取舍、验证和亲历范围",
    }


def _requirement_proposal(
    payload: dict[str, Any],
    requirement: dict[str, Any] | None,
    *,
    action: InterviewerAction,
    parent: str | None = None,
    answer: str = "",
    gaps: list | None = None,
    summary: str,
) -> dict[str, Any]:
    req = requirement or _synthetic_requirement(payload)
    req_id = str(req.get("id") or "req_01")
    if action is InterviewerAction.FOLLOW_UP:
        claim = probe_from_key(payload, payload.get("last_probe_key")) or claim_by_id(
            payload, (payload.get("last_claim_ids") or [None])[0]
        )
    else:
        claim = next_claim(payload)
    claim = claim or (claim_items(payload)[0] if claim_items(payload) else None)
    if not claim or not claim.get("probe_key"):
        return QuestionProposal(
            action=InterviewerAction.END,
            reason="AGENDA_COMPLETE",
            decision_summary="No valid question direction remains.",
        ).model_dump()
    return QuestionProposal(
        action=action,
        reason="NEW_DIRECTION" if action is InterviewerAction.ASK else "NEEDS_DETAIL",
        claim_id=claim["id"],
        probe_key=claim["probe_key"],
        topic_id=req_id,
        skill_ids=[req_id],
        question_text=compose_personalized_question(
            resume=payload.get("resume_plain") or "",
            jd=payload.get("jd_plain") or "",
            skill_ids=[req_id],
            action=action,
            last_answer=answer,
            requirement_text=req.get("text") or "",
            claim_text=(claim or {}).get("text") or "",
            probe_hint=(claim or {}).get("probe_hint") or "",
        ),
        parent_turn_id=parent,
        source_gap_ids=[
            g["id"]
            for g in (gaps or [])[:2]
            if isinstance(g, dict) and g.get("id") and g.get("skill_id") == req_id
        ],
        rubric_template_id=RUBRIC_EVIDENCE_ID,
        difficulty="intermediate",
        decision_summary=summary,
    ).model_dump()


def _extract_structure(text: str) -> dict[str, Any]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    skills = [
        line
        for line in lines
        if any(k in line.lower() for k in ("python", "fastapi", "rag", "postgres", "langgraph"))
    ]
    projects = [line for line in lines if any(k in line for k in ("项目", "系统", "平台", "agent"))]
    return {
        "summary": lines[0] if lines else "",
        "projects": [{"name": p[:80], "raw": p} for p in projects[:6]],
        "skills": skills[:12] or ["Python"],
        "claims": extract_claims(text),
    }
