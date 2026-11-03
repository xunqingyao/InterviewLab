import pytest

from domain.enums import InterviewerAction
from domain.schemas import QuestionProposal
from llm_gateway.factory import build_gateway
from llm_gateway.fake import FakeGateway, ReplayGateway
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext
from llm_gateway.providers import LiveGateway


def test_default_gateway_is_fake(monkeypatch):
    from db.settings import settings

    monkeypatch.setattr(settings, "model_provider", "fake")
    assert isinstance(build_gateway(), FakeGateway)


def test_replay_and_live_are_switchable(monkeypatch):
    from db.settings import settings

    monkeypatch.setattr(settings, "model_provider", "replay")
    assert isinstance(build_gateway(), ReplayGateway)
    monkeypatch.setattr(settings, "model_provider", "live")
    monkeypatch.setattr(settings, "model_api_key", "")
    gateway = build_gateway()
    assert isinstance(gateway, LiveGateway)


@pytest.mark.asyncio
async def test_live_without_key_uses_fake_fallback(monkeypatch):
    from db.settings import settings

    monkeypatch.setattr(settings, "model_provider", "live")
    monkeypatch.setattr(settings, "model_api_key", "")
    gateway = build_gateway()
    result = await gateway.generate_structured(
        role=AgentRole.INTERVIEWER,
        input_snapshot=AgentInputSnapshot(
            id="x",
            role=AgentRole.INTERVIEWER,
            payload={
                "templates": [
                    {
                        "template_key": "project_intro",
                        "topic_id": "topic_project",
                        "skill_ids": ["project.decision"],
                        "question_text": "请介绍项目",
                        "rubric_template_id": "rubric_project_intro_v1",
                        "difficulty": "intermediate",
                    }
                ],
                "answered_count": 0,
                "remaining_question_budget": 8,
                "max_questions": 8,
                "resume_plain": "主导 Python 异步任务队列",
                "jd_plain": "熟悉任务恢复",
            },
        ),
        output_schema=QuestionProposal,
        tools=[],
        budget=BudgetContext(owner_id="u1", session_id="s1"),
    )
    proposal = QuestionProposal.model_validate(result.data)
    assert proposal.action is InterviewerAction.ASK
    assert proposal.question_text


@pytest.mark.asyncio
async def test_live_http_error_does_not_silently_use_fake(monkeypatch):
    from db.settings import settings
    from domain.errors import DependencyUnavailable

    monkeypatch.setattr(settings, "model_provider", "live")
    monkeypatch.setattr(settings, "model_api_key", "sk-test")
    monkeypatch.setattr(settings, "max_session_cost", "5.00")
    monkeypatch.setattr(settings, "model_api_base", "https://example.invalid/v1")
    monkeypatch.setattr(settings, "model_identifier", "deepseek-flash")

    async def _boom(self, **kwargs):
        raise DependencyUnavailable("live model call failed for interviewer: boom")

    monkeypatch.setattr(LiveGateway, "_call_provider", _boom)
    from llm_gateway import budgets

    async def _no_database(*args, **kwargs):
        return None

    for name in ("acquire_slot", "release_slot", "reserve_session_budget", "settle_session_budget"):
        monkeypatch.setattr(budgets, name, _no_database)
    gateway = build_gateway()
    with pytest.raises(DependencyUnavailable):
        await gateway.generate_structured(
            role=AgentRole.INTERVIEWER,
            input_snapshot=AgentInputSnapshot(id="x", role=AgentRole.INTERVIEWER, payload={"templates": []}),
            output_schema=QuestionProposal,
            tools=[],
            budget=BudgetContext(owner_id="u1", session_id="s1"),
        )


@pytest.mark.asyncio
async def test_live_without_cost_limit_is_rejected(monkeypatch):
    from db.settings import settings
    from domain.errors import QuotaExceeded

    monkeypatch.setattr(settings, "model_provider", "live")
    monkeypatch.setattr(settings, "model_api_key", "sk-test")
    monkeypatch.setattr(settings, "max_session_cost", "")
    gateway = build_gateway()
    with pytest.raises(QuotaExceeded):
        await gateway.generate_structured(
            role=AgentRole.INTERVIEWER,
            input_snapshot=AgentInputSnapshot(id="x", role=AgentRole.INTERVIEWER, payload={"templates": []}),
            output_schema=QuestionProposal,
            tools=[],
            budget=BudgetContext(owner_id="u1", session_id="s1"),
        )
