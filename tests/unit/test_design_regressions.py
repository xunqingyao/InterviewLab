import pytest

from agents.context import build_coach_context, build_evaluator_context, build_interviewer_context
from agents.interviewer import validate_proposal
from domain.assessments import sanitize_assessment_result
from domain.claims import probe_from_key, unused_probes
from domain.enums import InterviewerAction
from domain.reports import aggregate_report
from domain.requirements import extract_requirements, ground_extracted_requirements
from domain.schemas import AssessmentResult, CoachProposal, QuestionProposal
from domain.training import relevant_claim, validate_training_proposal


def direction_snapshot():
    return {
        "requirements": {"items": [{"id": "req_01", "text": "幂等"}, {"id": "req_02", "text": "任务恢复"}]},
        "claims": {
            "items": [
                {
                    "id": "claim_01",
                    "text": "主导 Python 异步任务队列",
                    "probe_hints": ["约束", "验证", "失败"],
                }
            ]
        },
        "used_probe_keys": ["claim_01:0"],
        "used_requirement_ids": ["req_01"],
        "last_turn_id": "turn_1",
        "last_claim_ids": ["claim_01"],
        "last_probe_key": "claim_01:0",
        "last_skill_ids": ["req_01"],
        "last_answer": "做了队列。",
        "last_question": "如何避免重复提交？",
        "answered_count": 1,
        "remaining_question_budget": 7,
        "max_questions": 8,
    }


def question(**overrides):
    fields = dict(
        action="FOLLOW_UP",
        claim_id="claim_01",
        probe_key="claim_01:0",
        topic_id="req_01",
        skill_ids=["req_01"],
        parent_turn_id="turn_1",
        question_text="重复提交是怎么处理的？",
        rubric_template_id="rubric_evidence_v1",
        decision_summary="需要具体细节",
    )
    fields.update(overrides)
    return QuestionProposal(**fields)


@pytest.mark.parametrize(
    "overrides",
    [
        {"parent_turn_id": "wrong_turn"},
        {"probe_key": "claim_01:1"},
        {"topic_id": "req_02", "skill_ids": ["req_02"]},
        {"claim_id": "unknown_claim"},
    ],
)
def test_followup_must_keep_current_direction(overrides):
    result = validate_proposal(
        {"snapshot": direction_snapshot(), "proposal": question(**overrides).model_dump()}
    )
    proposal = QuestionProposal.model_validate(result["proposal"])
    assert proposal.action is InterviewerAction.ASK
    assert proposal.probe_key == "claim_01:1"
    assert proposal.skill_ids == ["req_02"]
    assert proposal.parent_turn_id is None


def test_used_probe_is_blocked_even_if_question_wording_changes():
    reused = question(action="ASK", parent_turn_id=None, question_text="换种说法再问一次。")
    result = validate_proposal({"snapshot": direction_snapshot(), "proposal": reused.model_dump()})
    assert result["proposal"]["probe_key"] == "claim_01:1"


def test_context_preserves_original_probe_keys_after_filtering():
    pack = build_interviewer_context(direction_snapshot())
    probes = pack["claims"]["unused"][0]["available_probes"]
    assert [row["probe_key"] for row in probes] == ["claim_01:1", "claim_01:2"]
    assert pack["claims"]["last_probe_key"] == "claim_01:0"
    assert probe_from_key(direction_snapshot(), "claim_01:99") is None
    assert len(unused_probes(direction_snapshot())) == 2


@pytest.mark.parametrize("action", ["CLARIFY", "SWITCH_TOPIC"])
def test_removed_actions_are_rejected(action):
    with pytest.raises(ValueError):
        question(action=action)


def test_latest_followup_replaces_old_gap_but_preserves_history():
    assessments = []
    for turn_no, level, status in [(1, 1, "PARTIAL"), (2, 3, "DEMONSTRATED")]:
        assessments.append(
            {
                "id": f"a{turn_no}",
                "turn_id": f"t{turn_no}",
                "turn_no": turn_no,
                "direction_id": "t1",
                "dimensions": [
                    {
                        "skill_id": "req_01",
                        "level": level,
                        "evidence_status": status,
                        "missing": "缺少验证" if turn_no == 1 else "",
                        "hint_level": "NONE",
                    }
                ],
                "verification_gaps": [{"skill_id": "req_01", "description": "缺少验证"}]
                if turn_no == 1
                else [],
            }
        )
    report = aggregate_report(
        session_id="s",
        mode="STRICT",
        cutoff_turn_no=2,
        missing_turn_ids=[],
        topics=[
            {"requirement_id": "req_01", "text": "幂等"},
            {"requirement_id": "req_02", "text": "任务恢复"},
        ],
        assessments=assessments,
    )
    current, unassessed = report["coverage"]
    assert current["levels"] == [3]
    assert current["sample_count"] == 1
    assert current["observation_count"] == 2
    assert len(current["history"]) == 2
    assert report["issues"] == []
    assert report["next_training"] == []
    assert report["next_assessment"] == ["req_02"]
    assert unassessed["assessment_status"] == "NOT_ASSESSED"


def test_evidence_binds_to_chain_answers_and_server_ids_only():
    result = AssessmentResult(
        turn_id="turn_2",
        rubric_snapshot_id="rubric_2",
        status="ASSESSED",
        dimensions=[
            {
                "skill_id": "req_01",
                "level": 3,
                "evidence_status": "DEMONSTRATED",
                "answer_evidence": [
                    {"message_id": "old_answer", "quote": "数据库唯一约束"},
                    {"message_id": "future", "quote": "未来回答"},
                ],
            },
        ],
    )
    cleaned = sanitize_assessment_result(
        result,
        answer_message_id="current",
        answer_text="做了并发测试。",
        answer_sources={"old_answer": "数据库唯一约束。", "current": "做了并发测试。"},
        expected_turn_id="turn_2",
        expected_rubric_id="rubric_2",
        allowed_skill_ids=["req_01"],
    )
    assert cleaned.turn_id == "turn_2"
    assert cleaned.rubric_snapshot_id == "rubric_2"
    assert len(cleaned.dimensions) == 1
    assert [span.message_id for span in cleaned.dimensions[0].answer_evidence] == ["old_answer"]


def test_evaluator_context_excludes_future_and_other_directions():
    pack = build_evaluator_context(
        {
            "turn_id": "t2",
            "cutoff_turn_no": 2,
            "skill_ids": ["req_01"],
            "answer_message_id": "m2",
            "answer_text": "当前回答",
            "thread": [{"turn_id": "t1", "turn_no": 1}, {"turn_id": "t2", "turn_no": 2}],
            "transcript": [
                {"turn_no": n, "kind": "ANSWER", "message_id": f"m{n}", "text": "回答"} for n in [0, 1, 2, 3]
            ],
        }
    )
    assert [row["message_id"] for row in pack["evidence_answers"]] == ["m1", "m2"]


def test_jd_relations_are_not_split_on_conjunctions():
    text = "理解事件循环与协程之间的关系；熟悉事务以及隔离级别的关系。"
    assert [row["text"] for row in extract_requirements(text)["items"]] == [
        "理解事件循环与协程之间的关系",
        "熟悉事务以及隔离级别的关系",
    ]
    grounded = ground_extracted_requirements(
        text, [{"text": "理解事件循环与协程之间的关系"}, {"text": "熟悉 Redis 集群"}]
    )
    assert len(grounded) == 1


def test_coach_selects_only_weak_requirements_and_real_or_no_claim():
    snapshot = direction_snapshot()
    snapshot["weak_requirements"] = [{"id": "req_02", "text": "任务恢复", "missing": ["没有失败重试验证"]}]
    assert build_coach_context(snapshot)["weak_requirements"][0]["missing"] == ["没有失败重试验证"]
    target = {
        "requirement_id": "req_02",
        "claim_id": None,
        "exercise_hint": "失败重试",
        "retest_hint": "重启接管",
    }
    validate_training_proposal(CoachProposal(targets=[target], rationale="练习恢复"), snapshot)
    for changed in [{"requirement_id": "req_01"}, {"claim_id": "invented"}, {"retest_hint": "失败重试"}]:
        with pytest.raises(ValueError):
            validate_training_proposal(
                CoachProposal(targets=[{**target, **changed}], rationale="练习恢复"), snapshot
            )
    assert relevant_claim("PostgreSQL 事务隔离", [{"id": "claim_01", "text": "主导 React 页面设计"}]) is None
