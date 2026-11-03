"""Turn this resume and one frozen job requirement into the published question."""

from __future__ import annotations

from typing import Any

from domain.claims import pick_probe
from domain.enums import InterviewerAction
from domain.requirements import GENERIC_PROBE, pick_requirement, unused_requirements
from domain.text import normalize_text, redact_identity


def _plain_lines(text: str) -> list[str]:
    lines = []
    for raw in redact_identity(text or "").splitlines():
        line = raw.strip()
        if not line or "DATA_ONLY" in line or line.startswith("<"):
            continue
        lines.append(line)
    if not lines:
        collapsed = " ".join((text or "").split())
        if collapsed:
            lines.append(collapsed[:240])
    return lines


def resume_anchors(resume: str, *, limit: int = 4) -> list[str]:
    lines = _plain_lines(resume)
    preferred = [
        line
        for line in lines
        if any(token in line for token in ("项目", "系统", "负责", "主导", "FastAPI", "PostgreSQL", "RAG", "队列", "Python"))
    ]
    chosen = (preferred or lines)[:limit]
    return [item[:120] for item in chosen]


def compose_personalized_question(
    *,
    resume: str,
    jd: str = "",
    skill_ids: list[str] | None = None,
    action: InterviewerAction | str,
    last_answer: str | None = None,
    requirement_text: str | None = None,
    claim_text: str | None = None,
    probe_hint: str | None = None,
) -> str:
    _ = skill_ids
    anchors = resume_anchors(resume)
    claim = (claim_text or "").strip() or (anchors[0] if anchors else "你简历中的项目经历")
    req = (requirement_text or "").strip() or "该岗位的核心要求"
    hint = (probe_hint or "").strip()
    name = action.value if isinstance(action, InterviewerAction) else str(action)
    if name == InterviewerAction.FOLLOW_UP.value:
        snip = normalize_text(last_answer or "").replace("\n", " ").strip()[:40] or "刚才的说法"
        return (
            f"你刚才提到「{snip}」。请结合简历中的「{claim}」和岗位要求「{req}」，"
            f"补充「{hint or '刚才题目中的关键步骤'}」的具体过程和原因，不要只说结论。"
        )
    if hint:
        return (
            f"你的简历写到「{claim}」。该岗位要求「{req}」。"
            f"请从这个角度说明：{hint}，讲清具体做法和原因。"
        )
    return f"你的简历写到「{claim}」。该岗位要求「{req}」。请结合这段经历，{GENERIC_PROBE}。"


def next_requirement(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    return pick_requirement(unused_requirements(snapshot))


def next_claim(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    return pick_probe(snapshot)


def compose_training_pair(
    *,
    requirement_text: str,
    claim_text: str,
    exercise_hint: str,
    retest_hint: str,
) -> tuple[str, str]:
    """Two live questions on the same requirement. Retest must not reuse the exercise wording."""
    if not claim_text.strip():
        return (
            f"针对岗位要求「{requirement_text}」，请说明：{exercise_hint}。",
            f"针对岗位要求「{requirement_text}」，请换一个场景说明：{retest_hint}。",
        )
    exercise = compose_personalized_question(
        resume=claim_text,
        skill_ids=[],
        action=InterviewerAction.ASK,
        requirement_text=requirement_text,
        claim_text=claim_text,
        probe_hint=exercise_hint,
    )
    retest = compose_personalized_question(
        resume=claim_text,
        skill_ids=[],
        action=InterviewerAction.ASK,
        requirement_text=requirement_text,
        claim_text=claim_text,
        probe_hint=retest_hint,
    )
    if normalize_text(retest) == normalize_text(exercise):
        retest = (
            f"复测（不要重复上一题的措辞）：你的简历写到「{claim_text or '你简历中的项目经历'}」。"
            f"该岗位要求「{requirement_text or '该岗位的核心要求'}」。"
            f"请另举一个例子说明：{retest_hint or '如何验证'}。"
        )
    return exercise, retest
