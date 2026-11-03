from domain.enums import InterviewerAction
from domain.schemas import QuestionProposal


def test_end_cannot_carry_question():
    p = QuestionProposal(action=InterviewerAction.END, decision_summary="budget reached")
    p.validate_semantics()
    try:
        QuestionProposal(
            action=InterviewerAction.END,
            question_text="still asking?",
            decision_summary="bad",
        ).validate_semantics()
        raise AssertionError
    except ValueError:
        pass


def test_follow_up_requires_parent():
    try:
        QuestionProposal(
            action=InterviewerAction.FOLLOW_UP,
            topic_id="project.decision",
            skill_ids=["project.decision"],
            claim_id="claim_01",
            probe_key="claim_01:0",
            question_text="请给出基线。",
            rubric_template_id="rubric_project_intro_v1",
            decision_summary="missing parent",
        ).validate_semantics()
        raise AssertionError
    except ValueError:
        pass


def test_ask_requires_question_direction_fields():
    QuestionProposal(
        action=InterviewerAction.ASK,
        topic_id="backend.idempotency",
        skill_ids=["backend.idempotency"],
        claim_id="claim_01",
        probe_key="claim_01:0",
        question_text="重试如何避免双写？",
        rubric_template_id="rubric_idempotency_v1",
        decision_summary="move on",
    ).validate_semantics()
