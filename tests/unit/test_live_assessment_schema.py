import json

import httpx
import pytest

from domain.schemas import AssessmentResult, QuestionProposal
from llm_gateway.fake import FakeGateway
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext
from llm_gateway.providers import LiveGateway, _output_schema_for_context


@pytest.mark.parametrize("requirement_id", ["req_01", "req_02"])
def test_model_schema_binds_ids_to_the_current_question(requirement_id):
    schema = _output_schema_for_context(
        AssessmentResult,
        {
            "turn_id": "turn-1",
            "rubric_snapshot_id": "rubric-1",
            "requirement": {"id": requirement_id, "text": "Agent 开发"},
        },
    )
    assert schema["properties"]["turn_id"]["const"] == "turn-1"
    assert schema["properties"]["rubric_snapshot_id"]["const"] == "rubric-1"
    for name in ("DimensionAssessment", "VerificationGap"):
        assert schema["$defs"][name]["properties"]["skill_id"]["const"] == requirement_id
    assert schema["properties"]["dimensions"]["maxItems"] == 1
    # Binding a request must not mutate the general schema or another request.
    assert "const" not in AssessmentResult.model_json_schema()["properties"]["turn_id"]
    assert AssessmentResult.model_json_schema()["properties"]["dimensions"]["maxItems"] == 16


def test_non_assessment_schema_is_unchanged():
    assert _output_schema_for_context(QuestionProposal, {}) == QuestionProposal.model_json_schema()


@pytest.mark.asyncio
async def test_live_provider_request_includes_bound_schema_without_network(monkeypatch):
    from db.settings import settings

    monkeypatch.setattr(settings, "model_api_base", "https://example.invalid/v1")
    captured = {}

    async def post(_, url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "turn_id": "turn-1",
                                    "rubric_snapshot_id": "rubric-1",
                                    "status": "INSUFFICIENT_EVIDENCE",
                                    "dimensions": [],
                                    "verification_gaps": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    result = await LiveGateway(FakeGateway())._call_provider(
        role=AgentRole.EVALUATOR,
        input_snapshot=AgentInputSnapshot(
            id="snapshot-1",
            role=AgentRole.EVALUATOR,
            payload={
                "turn_id": "turn-1",
                "rubric_snapshot_id": "rubric-1",
                "requirement": {"id": "req_01", "text": "Agent 开发"},
            },
        ),
        output_schema=AssessmentResult,
        tools=[],
        budget=BudgetContext(owner_id="owner-1"),
    )
    instructions = json.loads(captured["messages"][1]["content"])
    skill_schema = instructions["schema"]["$defs"]["DimensionAssessment"]["properties"]["skill_id"]
    assert skill_schema["const"] == "req_01"
    assert result.data["turn_id"] == "turn-1"
