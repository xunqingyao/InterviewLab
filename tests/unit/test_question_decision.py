import pytest
from evals.agent_suite import suite_cases

from agents.context import build_interviewer_context
from domain.errors import InvalidAgentOutput
from domain.questions import materialize_question_decision
from domain.schemas import QuestionDecision
from llm_gateway.providers import _output_schema_for_context


def followup_snapshot():
    return next(c["snapshot"] for c in suite_cases() if c["case_id"] == "interview_followup")


def draft(**changes):
    return {
        "action": "FOLLOW_UP",
        "question_text": "你提到按角色拆开了，哪些内容必须由代码校验？",
        "decision_summary": "澄清职责边界",
        **changes,
    }


def test_server_owns_followup_direction_even_with_unused_probe_angles():
    snap = followup_snapshot()
    proposal = materialize_question_decision(draft(), snap)
    assert proposal.probe_key == snap["last_probe_key"]
    assert proposal.claim_id == snap["last_claim_ids"][0]
    assert proposal.parent_turn_id == snap["last_turn_id"]
    assert proposal.skill_ids == snap["last_skill_ids"]
    assert proposal.rubric_template_id == "rubric_question_v2"


@pytest.mark.parametrize(
    "extra",
    [
        {"probe_key": "claim_01:1"},
        {"claim_id": "other-claim"},
        {"requirement_id": "req_02"},
        {"parent_turn_id": "other-turn"},
    ],
)
def test_model_cannot_override_server_owned_followup_ids(extra):
    with pytest.raises(ValueError):
        materialize_question_decision(draft(**extra), followup_snapshot())


def test_binding_does_not_bypass_depth_or_answer_guards():
    snap = followup_snapshot()
    for changes in ({"followup_depth": 2}, {"last_answer": ""}, {"last_probe_key": "unknown"}):
        with pytest.raises((ValueError, InvalidAgentOutput)):
            materialize_question_decision(draft(), {**snap, **changes})


def test_ask_still_requires_unused_authorized_direction():
    snap = followup_snapshot()
    raw = {
        "action": "ASK",
        "claim_id": "claim_01",
        "probe_key": "claim_01:0",
        "requirement_id": "req_01",
        "question_text": "请解释边界。",
        "decision_summary": "新方向",
    }
    with pytest.raises(InvalidAgentOutput):
        materialize_question_decision(raw, snap)
    raw["probe_key"] = "claim_01:1"
    proposal = materialize_question_decision(raw, snap)
    assert proposal.parent_turn_id is None and proposal.probe_key == "claim_01:1"


def test_model_schema_exposes_current_ask_options_and_no_followup_ids():
    schema = _output_schema_for_context(QuestionDecision, build_interviewer_context(followup_snapshot()))
    assert schema["$defs"]["AskDecision"]["properties"]["probe_key"]["enum"] == ["claim_01:1", "claim_01:2"]
    assert set(schema["$defs"]["FollowUpDecision"]["properties"]) == {
        "action",
        "question_text",
        "reason",
        "decision_summary",
    }
    assert schema["$defs"]["FollowUpDecision"]["additionalProperties"] is False
