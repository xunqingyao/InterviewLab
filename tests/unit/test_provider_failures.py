import json

import httpx
import pytest
from evals.agent_suite import call_live
from evals.evaluator_quality import model_context
from evals.quality_cases import quality_cases

import llm_gateway.providers as providers
from db.settings import settings
from domain.errors import DependencyUnavailable
from domain.schemas import AssessmentResult
from llm_gateway.fake import FakeGateway
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext


def gateway_args():
    snapshot = quality_cases()[2]["snapshot"]
    return {
        "role": AgentRole.EVALUATOR,
        "input_snapshot": AgentInputSnapshot(
            id="transport-test", role=AgentRole.EVALUATOR, payload=model_context(snapshot)
        ),
        "output_schema": AssessmentResult,
        "tools": [],
        "budget": BudgetContext(owner_id="test"),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["timeout", "rate_limit", "server_error", "bad_json", "bad_schema", "empty"]
)
async def test_live_model_failures_are_not_silently_scored_or_faked(monkeypatch, failure):
    original_client = httpx.AsyncClient

    async def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("test timeout", request=request)
        if failure in {"rate_limit", "server_error"}:
            return httpx.Response(429 if failure == "rate_limit" else 503, json={"error": "test"})
        content = {"bad_json": "not json", "bad_schema": json.dumps({"turn_id": "x"}), "empty": ""}[failure]
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(
        providers.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(settings, "model_api_base", "https://model.test/v1")
    with pytest.raises(DependencyUnavailable):
        await providers.LiveGateway(FakeGateway())._call_provider(**gateway_args())


@pytest.mark.asyncio
async def test_eval_transport_preserves_malformed_output_for_diagnosis(monkeypatch):
    monkeypatch.setattr(settings, "model_api_base", "https://model.test/v1")
    monkeypatch.setattr(settings, "model_api_key", "not-a-real-key")
    record = {}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "model": "test-version",
                    "usage": {"total_tokens": 9},
                    "choices": [{"message": {"content": "malformed output"}}],
                },
            )
        )
    ) as client:
        with pytest.raises(json.JSONDecodeError):
            await call_live(client, {}, record)
    assert record["raw_content"] == "malformed output"
    assert record["returned_model"] == "test-version"
    assert record["usage"]["total_tokens"] == 9
    assert "not-a-real-key" not in json.dumps(record)
