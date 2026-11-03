from copy import deepcopy

import pytest
from tests.unit.test_design_regressions import direction_snapshot, question

from domain.assessments import sanitize_assessment_result
from domain.enums import EvidenceStatus, HintLevel
from domain.errors import InvalidAgentOutput
from domain.questions import validate_question_binding
from domain.rubrics import build_question_rubric, select_profile
from domain.schemas import AssessmentResult


@pytest.mark.parametrize(
    "text,expected",
    [
        ("事件循环与协程之间有什么关系？", "concept"),
        ("为什么用 PostgreSQL 不用 Redis？", "tradeoff"),
        ("任务重复执行时如何定位和修复？", "troubleshooting"),
        ("如何验证你的幂等处理有效？", "verification"),
        ("这部分你本人做了哪些工作？", "ownership"),
        ("请说明异步任务提交到执行的步骤。", "implementation"),
    ],
)
def test_question_type_selects_appropriate_rubric(text, expected):
    assert select_profile(text) == expected


def test_resume_quote_does_not_change_question_type():
    assert select_profile("简历写到「排查故障、验证指标」。为什么用这个方案不用那个？") == "tradeoff"


def test_implementation_with_constraints_is_not_forced_into_alternative_comparison():
    assert select_profile("解释幂等的实现过程和关键约束", "实现过程和关键约束") == "implementation"
    assert select_profile("分析任务失败场景及验证办法", "失败场景及验证办法") == "troubleshooting"


def test_concept_rubric_does_not_require_experiment_or_personal_ownership():
    rubric = build_question_rubric(
        question_text="事件循环与协程之间是什么关系？",
        requirement_id="req_01",
        requirement_text="理解异步编程",
    )
    assert rubric["profile"] == "concept"
    assert "个人项目贡献" in rubric["not_required"]
    assert "实验测试集" in rubric["not_required"]
    assert "技术内容" in "".join(rubric["rules"])
    assert "离题" in rubric["levels"]["0"]
    assert "level 为 null" in rubric["null_rule"]
    assert rubric["scope"]["requirement_id"] == "req_01"


def test_rubric_is_independent_and_has_no_answer_argument():
    arguments = dict(question_text="如何验证？", requirement_id="req_01", requirement_text="幂等")
    first = build_question_rubric(**arguments)
    first["checks"].clear()
    assert build_question_rubric(**arguments)["checks"]


def assessment(**overrides):
    fields = dict(
        turn_id="turn_1",
        rubric_snapshot_id="rubric_1",
        status="ASSESSED",
        dimensions=[
            {
                "skill_id": "req_01",
                "level": 2,
                "evidence_status": "DEMONSTRATED",
                "answer_evidence": [{"message_id": "msg_1", "quote": "数据库唯一约束"}],
            },
        ],
    )
    fields.update(overrides)
    return AssessmentResult(**fields)


def sanitize(result):
    return sanitize_assessment_result(
        result,
        answer_message_id="msg_1",
        answer_text="数据库唯一约束。",
        expected_turn_id="turn_1",
        expected_rubric_id="rubric_1",
        allowed_skill_ids=["req_01"],
        allowed_reference_ids=["ref_1"],
        expected_hint_level="EXAMPLE",
    )


@pytest.mark.parametrize("overrides", [{"turn_id": "another_turn"}, {"rubric_snapshot_id": "another_rubric"}])
def test_wrong_turn_or_rubric_is_rejected_not_rewritten(overrides):
    with pytest.raises(InvalidAgentOutput):
        sanitize(assessment(**overrides))


def test_other_requirement_or_duplicate_dimension_is_rejected():
    dimension = assessment().dimensions[0].model_dump()
    for dimensions in [[{**dimension, "skill_id": "req_02"}], [dimension, deepcopy(dimension)]]:
        with pytest.raises(InvalidAgentOutput):
            sanitize(assessment(dimensions=dimensions))
    with pytest.raises(InvalidAgentOutput):
        sanitize(assessment(verification_gaps=[{"skill_id": "req_02", "description": "另一个方向的缺口"}]))


def test_matching_quote_cannot_repair_an_unauthorized_message_id():
    result = assessment()
    result.dimensions[0].answer_evidence[0].message_id = "another_answer"
    cleaned = sanitize(result)
    assert cleaned.dimensions[0].answer_evidence == []
    assert cleaned.dimensions[0].level is None


def test_reference_ids_and_hint_level_are_server_owned():
    result = assessment()
    result.dimensions[0].reference_chunk_ids = ["ref_1", "outside_snapshot", "ref_1"]
    cleaned = sanitize(result)
    assert cleaned.dimensions[0].reference_chunk_ids == ["ref_1"]
    assert cleaned.dimensions[0].hint_level is HintLevel.EXAMPLE


@pytest.mark.parametrize("status", ["INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE", "REFERENCE_CONFLICT"])
def test_non_scoring_status_cannot_keep_a_numeric_level(status):
    result = assessment()
    result.dimensions[0].evidence_status = EvidenceStatus(status)
    assert sanitize(result).dimensions[0].level is None


def test_empty_authorized_sources_does_not_fall_back_to_current_answer():
    result = sanitize_assessment_result(
        assessment(), answer_message_id="msg_1", answer_text="数据库唯一约束。", answer_sources={}
    )
    assert result.dimensions[0].level is None


def test_interviewer_cannot_select_arbitrary_grading_policy_or_gap():
    for overrides in [{"rubric_template_id": "easy_rubric"}, {"source_gap_ids": ["other_gap"]}]:
        with pytest.raises(InvalidAgentOutput):
            validate_question_binding(question(**overrides), direction_snapshot())


def test_end_cannot_hide_question_bindings():
    with pytest.raises(ValueError):
        question(action="END", question_text=None).validate_semantics()
