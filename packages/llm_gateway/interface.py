from __future__ import annotations

from typing import Any, Protocol, Sequence

from pydantic import BaseModel

from domain.enums import InterviewerAction
from domain.ids import new_id


class AgentRole:
    INTERVIEWER = "interviewer"
    EVALUATOR = "evaluator"
    COACH = "coach"
    EXTRACTOR = "extractor"
    TRANSCRIBER = "transcriber"


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class AgentInputSnapshot(BaseModel):
    id: str
    role: str
    payload: dict[str, Any]


class BudgetContext(BaseModel):
    owner_id: str
    session_id: str | None = None
    max_calls: int = 4
    max_output_tokens: int = 1200


class GatewayResult(BaseModel):
    data: dict[str, Any]
    model: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    repaired: bool = False
    provider_request_id: str | None = None


class LLMGateway(Protocol):
    async def generate_structured(
        self,
        *,
        role: str,
        input_snapshot: AgentInputSnapshot,
        output_schema: type[BaseModel],
        tools: Sequence[ToolSpec],
        budget: BudgetContext,
    ) -> GatewayResult: ...
