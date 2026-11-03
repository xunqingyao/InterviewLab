from copy import deepcopy

import pytest
from evals.agent_suite import SECRET, request_spec, suite_cases, validate_output

from domain.claims import ground_extracted_claims
from domain.schemas import CoachProposal
from domain.training import validate_training_proposal
from llm_gateway.fake import FakeGateway
from llm_gateway.interface import BudgetContext


@pytest.mark.asyncio
@pytest.mark.parametrize("case", suite_cases(), ids=lambda case: case["case_id"])
async def test_all_roles_obey_final_output_contracts(case):
    snapshot, schema, tools = request_spec(case)
    result = await FakeGateway().generate_structured(
        role=case["role"],
        input_snapshot=snapshot,
        output_schema=schema,
        tools=tools,
        budget=BudgetContext(owner_id="unit-test"),
    )
    checked = validate_output(case, result.data)
    assert checked["postprocessed_valid"], checked["checks"]


@pytest.mark.parametrize(
    "role, forbidden",
    [
        ("interviewer", SECRET),
        ("interviewer", "PRIVATE_COACH_SENTINEL"),
        ("coach", SECRET),
        ("coach", "HINTED_ONLY_SENTINEL"),
    ],
)
def test_full_context_including_tool_results_keeps_role_boundaries(role, forbidden):
    import json

    for case in suite_cases():
        if case["role"] == role:
            snapshot, _, _ = request_spec(case)
            assert forbidden not in json.dumps(snapshot.payload, ensure_ascii=False)


def test_verbatim_prefix_recovery_uses_original_suffix():
    prefix = "负责模型上下文隔离及评分证据校验并将每次运行的状态与业务写入进行分离以支持故障后的任务恢复机制"
    resume = prefix + "，实际只参与了接口实现。"
    fabricated = prefix + "，主导了从未出现过的千万并发优化。"
    claims = ground_extracted_claims(resume, [{"text": fabricated}])
    assert claims and claims[0]["text"] in resume
    assert "千万并发" not in claims[0]["text"]


def test_ambiguous_prefix_does_not_select_a_different_project():
    prefix = "负责模型上下文隔离及评分证据校验并将每次运行的状态与业务写入进行分离以支持故障后的任务恢复机制"
    resume = prefix + "，在项目甲负责接口。\n" + prefix + "，在项目乙负责报告。"
    assert ground_extracted_claims(resume, [{"text": prefix + "，模型编造的后缀。"}]) == []


@pytest.mark.parametrize("field", ["exercise_hint", "retest_hint"])
def test_coach_cannot_publish_whitespace_only_training_angles(field):
    case = next(case for case in suite_cases() if case["case_id"] == "coach_without_resume")
    target = {
        "requirement_id": "req_01",
        "claim_id": None,
        "exercise_hint": "实现步骤",
        "retest_hint": "验证失败边界",
    }
    target[field] = "   "
    proposal = CoachProposal(targets=[target], rationale="针对已确认弱项训练")
    with pytest.raises(ValueError, match="blank"):
        validate_training_proposal(proposal, case["snapshot"])


def test_bad_interviewer_proposal_is_visible_as_fallback_not_model_success():
    case = next(case for case in suite_cases() if case["case_id"] == "interview_first")
    checked = validate_output(case, {"action": "ASK", "decision_summary": "missing binding"})
    assert checked["fallback_used"]
    assert not checked["raw_contract_valid"]
    assert checked["postprocessed_valid"]


@pytest.mark.parametrize("bad_id", ["req_not_weak", "req_02"])
def test_coach_cannot_promote_unconfirmed_training_targets(bad_id):
    case = deepcopy(next(case for case in suite_cases() if case["case_id"] == "coach_without_resume"))
    with pytest.raises(ValueError, match="confirmed weak"):
        validate_output(
            case,
            {
                "targets": [
                    {
                        "requirement_id": bad_id,
                        "claim_id": None,
                        "exercise_hint": "实现步骤",
                        "retest_hint": "失败边界",
                    }
                ],
                "rationale": "训练",
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [{"remaining_question_budget": 0}, {"remaining_seconds": 0}])
async def test_budget_guard_never_calls_model(monkeypatch, budget):
    import agents.interviewer as interviewer

    snapshot = deepcopy(
        next(case for case in suite_cases() if case["case_id"] == "interview_first")["snapshot"]
    )
    snapshot.update(budget)

    def forbidden_gateway():
        pytest.fail("exhausted budget must be handled before creating a model request")

    monkeypatch.setattr(interviewer, "build_gateway", forbidden_gateway)
    result = await interviewer.interviewer_graph().ainvoke({"snapshot": snapshot, "owner_id": "unit"})
    assert result["proposal"]["action"] == "END"
