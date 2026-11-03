import pytest

from agents import propose_next_question
from agents.tools import consult_interviewer_tools, list_job_requirements
from domain.enums import InterviewerAction
from domain.text import redact_identity


TEMPLATES = [
    {
        "template_key": "project_intro",
        "topic_id": "topic_project",
        "skill_ids": ["project.decision"],
        "question_text": "请介绍一个你主导的项目：为什么采用这个设计？",
        "rubric_template_id": "rubric_project_intro_v1",
        "difficulty": "intermediate",
    },
    {
        "template_key": "idempotency",
        "topic_id": "topic_backend",
        "skill_ids": ["backend.idempotency"],
        "question_text": "用户重试提交时如何避免两条答案？",
        "rubric_template_id": "rubric_idempotency_v1",
        "difficulty": "intermediate",
    },
    {
        "template_key": "idempotency_retest",
        "topic_id": "topic_backend",
        "skill_ids": ["backend.idempotency"],
        "question_text": "租约过期后旧 Worker 回来了怎么办？",
        "rubric_template_id": "rubric_idempotency_v1",
        "difficulty": "advanced",
    },
]


def _snapshot(**overrides):
    base = {
        "id": "snap-1",
        "templates": TEMPLATES,
        "requirements": {
            "source": "backend",
            "items": [
                {"id": "req_01", "priority": "must", "text": "幂等", "probe_angle": "说明约束、取舍、验证和亲历范围"},
                {"id": "req_02", "priority": "must", "text": "任务恢复", "probe_angle": "说明约束、取舍、验证和亲历范围"},
            ],
        },
        "used_requirement_ids": [],
        "used_template_keys": [],
        "used_question_texts": [],
        "answered_count": 0,
        "remaining_question_budget": 8,
        "max_questions": 8,
        "followup_depth": 0,
        "resume_plain": "梁开新主导过 FastAPI 任务队列，电话 13812345678，邮箱 a@b.com",
        "jd_plain": "需要幂等与任务恢复",
        "claims": {
            "items": [
                {"id": "claim_01", "kind": "project", "text": "梁开新主导过 FastAPI 任务队列"},
            ]
        },
        "used_claim_ids": [],
        "gaps": [],
    }
    base.update(overrides)
    if base["answered_count"]:
        base.setdefault("last_claim_ids", ["claim_01"])
        base.setdefault("last_probe_key", "claim_01:0")
        base.setdefault("used_probe_keys", ["claim_01:0"])
    return base


@pytest.mark.asyncio
async def test_first_question_uses_resume_and_jd_not_catalog():
    proposal = await propose_next_question(_snapshot(), owner_id="u1", session_id="s1")
    catalog = {item["question_text"] for item in TEMPLATES}
    assert proposal.action is InterviewerAction.ASK
    assert proposal.question_text not in catalog
    assert "FastAPI" in (proposal.question_text or "") or "任务队列" in (proposal.question_text or "")
    assert "幂等" in (proposal.question_text or "")
    assert proposal.skill_ids == ["req_01"]
    assert "13812345678" not in (proposal.question_text or "")
    assert "a@b.com" not in (proposal.question_text or "")


@pytest.mark.asyncio
async def test_vague_answer_follow_up():
    proposal = await propose_next_question(
        _snapshot(
            answered_count=1,
            remaining_question_budget=7,
            last_answer="我们优化了效果，感觉快了很多。",
            last_turn_id="turn-1",
            last_topic_id="topic_project",
            last_skill_ids=["req_01"],
            last_rubric_template_id="rubric_evidence_v1",
            last_question=TEMPLATES[0]["question_text"],
            used_requirement_ids=["req_01"],
            used_question_texts=[TEMPLATES[0]["question_text"]],
        ),
        owner_id="u1",
        session_id="s1",
    )
    assert proposal.action is InterviewerAction.FOLLOW_UP
    assert proposal.parent_turn_id == "turn-1"
    assert "优化了效果" in (proposal.question_text or "")
    assert "FastAPI" in (proposal.question_text or "") or "任务队列" in (proposal.question_text or "")


@pytest.mark.asyncio
async def test_short_answer_follow_up():
    proposal = await propose_next_question(
        _snapshot(
            answered_count=1,
            remaining_question_budget=7,
            last_answer="做了队列。",
            last_turn_id="turn-1",
            last_rubric_template_id="rubric_evidence_v1",
            last_topic_id="req_01",
            last_skill_ids=["req_01"],
            used_requirement_ids=["req_01"],
            used_question_texts=[TEMPLATES[0]["question_text"]],
        ),
        owner_id="u1",
        session_id="s1",
    )
    assert proposal.action is InterviewerAction.FOLLOW_UP
    assert proposal.parent_turn_id == "turn-1"


@pytest.mark.asyncio
async def test_budget_exhausted_ends():
    proposal = await propose_next_question(
        _snapshot(answered_count=8, remaining_question_budget=0, remaining_seconds=0),
        owner_id="u1",
        session_id="s1",
    )
    assert proposal.action is InterviewerAction.END
    assert proposal.question_text is None


@pytest.mark.asyncio
async def test_followup_depth_falls_back_to_ask():
    proposal = await propose_next_question(
        _snapshot(
            answered_count=1,
            remaining_question_budget=6,
            followup_depth=2,
            last_answer="我们优化了效果，感觉很快。",
            last_turn_id="turn-1",
            last_rubric_template_id="rubric_project_intro_v1",
            last_topic_id="topic_project",
            last_skill_ids=["req_01"],
            used_requirement_ids=["req_01"],
            used_question_texts=[TEMPLATES[0]["question_text"]],
        ),
        owner_id="u1",
        session_id="s1",
    )
    assert proposal.action is InterviewerAction.ASK
    assert proposal.question_text != TEMPLATES[1]["question_text"]
    assert proposal.skill_ids == ["req_02"]
    assert "任务恢复" in (proposal.question_text or "")


def test_list_job_requirements_not_a_question_bank():
    found = list_job_requirements(_snapshot(used_requirement_ids=["req_01"], last_skill_ids=["req_01"]))
    unused_ids = [item["id"] for item in found["unused"]]
    assert unused_ids == ["req_02"]
    assert found["current"]["id"] == "req_01"
    assert found["current"]["text"] == "幂等"
    for item in found["unused"]:
        assert "question_text" not in item


def test_consult_tools_capped_and_redacts_via_snapshot_plain_text():
    snap = _snapshot()
    results = consult_interviewer_tools(snap, owner_id="injected-owner")
    assert len(results) == 3
    assert "get_agenda_status" in results
    assert "get_resume_claims" in results
    assert "list_job_requirements" in results
    assert "search_question_templates" not in results
    redacted = redact_identity(snap["resume_plain"])
    assert "[REDACTED_PHONE]" in redacted
    assert "[REDACTED_EMAIL]" in redacted
    assert "13812345678" not in redacted
