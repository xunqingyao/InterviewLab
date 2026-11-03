from types import SimpleNamespace

import pytest

from domain.schemas import CoachProposal
from domain.training import TrainingProposalError, validate_training_proposal
from llm_gateway.providers import _output_schema_for_context


def proposal(requirement_id="req_06"):
    return CoachProposal(
        targets=[
            dict(
                requirement_id=requirement_id, claim_id=None, exercise_hint="实现过程", retest_hint="失败恢复"
            )
        ],
        rationale="补齐证据",
    )


def test_coach_schema_restricts_ids_and_target_count():
    schema = _output_schema_for_context(
        CoachProposal,
        {
            "weak_requirements": [{"id": "req_06"}],
            "claims": [{"id": "claim_02"}],
        },
    )
    target = schema["$defs"]["TrainingTarget"]["properties"]
    assert target["requirement_id"]["enum"] == ["req_06"]
    assert target["claim_id"]["anyOf"][0]["enum"] == ["claim_02"]
    assert schema["properties"]["targets"]["maxItems"] == 1
    assert (
        "enum"
        not in CoachProposal.model_json_schema()["$defs"]["TrainingTarget"]["properties"]["requirement_id"]
    )


@pytest.mark.asyncio
async def test_coach_repairs_invalid_target_once(monkeypatch):
    import llm_gateway.factory
    from agents import propose_training

    calls = []

    async def generate(**kwargs):
        calls.append(kwargs["input_snapshot"].payload)
        return SimpleNamespace(data=proposal("ex:req_06" if len(calls) == 1 else "req_06").model_dump())

    monkeypatch.setattr(
        llm_gateway.factory, "build_gateway", lambda: SimpleNamespace(generate_structured=generate)
    )
    result = await propose_training(
        {"weak_requirements": [{"id": "req_06"}]}, owner_id="owner", session_id="session"
    )
    assert result.targets[0].requirement_id == "req_06"
    assert len(calls) == 2
    assert calls[1]["validation_feedback"]["code"] == "INVALID_TARGET"


@pytest.mark.asyncio
async def test_coach_does_not_loop_forever_or_accept_invalid_result(monkeypatch):
    import llm_gateway.factory
    from agents import propose_training

    calls = []

    async def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=proposal("unknown").model_dump())

    monkeypatch.setattr(
        llm_gateway.factory, "build_gateway", lambda: SimpleNamespace(generate_structured=generate)
    )
    with pytest.raises(TrainingProposalError) as error:
        await propose_training({"weak_requirements": [{"id": "req_06"}]}, owner_id="o", session_id="s")
    assert error.value.reason_code == "INVALID_TARGET"
    assert len(calls) == 2


def test_duplicate_angles_explain_failure():
    value = proposal()
    value.targets[0].retest_hint = value.targets[0].exercise_hint
    with pytest.raises(TrainingProposalError) as error:
        validate_training_proposal(value, {"weak_skills": ["req_06"]})
    assert error.value.reason_code == "DUPLICATE_ANGLE"
