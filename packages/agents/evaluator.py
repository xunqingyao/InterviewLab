from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from agents.context import build_evaluator_context
from agents.evaluator_tools import EVALUATOR_TOOL_SPECS, consult_evaluator_tools
from agents.prompts import EVALUATOR_V3
from domain.assessments import sanitize_assessment_result
from domain.policies import wrap_untrusted
from domain.schemas import AssessmentResult
from llm_gateway.factory import build_gateway
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext


class EvaluatorState(TypedDict, total=False):
    snapshot: dict
    owner_id: str
    session_id: str
    tool_results: dict
    result: dict
    prompt_version: str


def load_snapshot(state: EvaluatorState) -> EvaluatorState:
    snap = dict(state.get("snapshot") or {})
    snap.pop("interviewer_decision", None)
    snap.pop("coach_feedback", None)
    snap.pop("other_assessment_scores", None)
    if snap.get("answer_text"):
        snap["answer_wrapped"] = wrap_untrusted("ANSWER_DATA", snap["answer_text"])
    if snap.get("question_text"):
        snap["question_wrapped"] = wrap_untrusted("QUESTION_DATA", snap["question_text"])
    snap["evaluator_prompt_version"] = "evaluator_v3"
    return {"snapshot": snap, "prompt_version": "evaluator_v3"}


def consult_tools(state: EvaluatorState) -> EvaluatorState:
    snap = dict(state["snapshot"])
    results = consult_evaluator_tools(snap, owner_id=state["owner_id"])
    snap["tool_results"] = results
    return {"snapshot": snap, "tool_results": results}


async def decide(state: EvaluatorState) -> EvaluatorState:
    pack = build_evaluator_context(state["snapshot"])
    pack["tool_results"] = state.get("tool_results") or state["snapshot"].get("tool_results") or {}
    gateway = build_gateway()
    result = await gateway.generate_structured(
        role=AgentRole.EVALUATOR,
        input_snapshot=AgentInputSnapshot(
            id=state["snapshot"].get("id", "snap"),
            role=AgentRole.EVALUATOR,
            payload=pack,
        ),
        output_schema=AssessmentResult,
        tools=EVALUATOR_TOOL_SPECS,
        budget=BudgetContext(owner_id=state["owner_id"], session_id=state.get("session_id")),
    )
    return {"result": result.data}


def validate_result(state: EvaluatorState) -> EvaluatorState:
    snap = state["snapshot"]
    parsed = AssessmentResult.model_validate(state.get("result") or {})
    cleaned = sanitize_assessment_result(
        parsed,
        answer_message_id=str(snap.get("answer_message_id") or ""),
        answer_text=snap.get("answer_text") or "",
        answer_sources={
            str(row["message_id"]): row["text"]
            for row in build_evaluator_context(snap)["evidence_answers"]
            if row.get("message_id")
        }
        or None,
        expected_turn_id=str(snap.get("turn_id") or ""),
        expected_rubric_id=str(snap.get("rubric_snapshot_id") or ""),
        allowed_skill_ids=list(snap.get("skill_ids") or []),
        allowed_reference_ids=[row["id"] for row in snap.get("reference_hits") or [] if row.get("id")],
        expected_hint_level=snap.get("hint_level") or "NONE",
    )
    return {"result": cleaned.model_dump()}


def evaluator_graph(checkpointer=None):
    graph = StateGraph(EvaluatorState)
    graph.add_node("load_snapshot", load_snapshot)
    graph.add_node("consult_tools", consult_tools)
    graph.add_node("decide", decide)
    graph.add_node("validate_result", validate_result)
    graph.add_edge(START, "load_snapshot")
    graph.add_edge("load_snapshot", "consult_tools")
    graph.add_edge("consult_tools", "decide")
    graph.add_edge("decide", "validate_result")
    graph.add_edge("validate_result", END)
    return graph.compile(checkpointer=checkpointer)


assert EVALUATOR_V3
