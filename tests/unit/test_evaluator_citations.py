from domain.assessments import sanitize_assessment_result
from domain.enums import AssessmentStatus, EvidenceStatus, MessageKind
from domain.schemas import AssessmentResult, DimensionAssessment, TextSpan, bind_citation


def test_backend_locates_quote_using_authorized_message_id():
    text = "我会先固定测试集和生成配置。"
    span = TextSpan(message_id="msg-answer", quote="固定测试集", start_char=999, end_char=1000)
    result = sanitize_assessment_result(
        AssessmentResult(
            turn_id="t1",
            rubric_snapshot_id="r1",
            status="ASSESSED",
            dimensions=[
                DimensionAssessment(
                    skill_id="rag.evaluation_design",
                    level=2,
                    evidence_status=EvidenceStatus.DEMONSTRATED,
                    answer_evidence=[span],
                )
            ],
        ),
        answer_message_id="msg-answer",
        answer_text=text,
    )
    bound = result.dimensions[0].answer_evidence[0]
    assert bound.message_id == "msg-answer"
    assert bound.start_char is not None
    assert text[bound.start_char : bound.end_char] == "固定测试集"


def test_invalid_quote_drops_level_to_null_not_zero():
    result = sanitize_assessment_result(
        AssessmentResult(
            turn_id="t1",
            rubric_snapshot_id="r1",
            status="ASSESSED",
            dimensions=[
                DimensionAssessment(
                    skill_id="rag.evaluation_design",
                    level=3,
                    evidence_status=EvidenceStatus.DEMONSTRATED,
                    answer_evidence=[TextSpan(message_id="msg-answer", quote="这段话并不存在于答案里")],
                )
            ],
        ),
        answer_message_id="msg-answer",
        answer_text="我们优化了效果。",
    )
    dim = result.dimensions[0]
    assert dim.level is None
    assert dim.level != 0
    assert dim.evidence_status is EvidenceStatus.INSUFFICIENT_EVIDENCE
    assert dim.answer_evidence == []
    assert result.status == AssessmentStatus.INSUFFICIENT_EVIDENCE.value


def test_ambiguous_quote_rejected():
    try:
        bind_citation(TextSpan(message_id="m", quote="好。"), MessageKind.ANSWER, "好。好。")
        raise AssertionError
    except ValueError:
        pass
