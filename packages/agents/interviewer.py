from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.context import build_interviewer_context
from agents.personalize import compose_personalized_question, next_claim, next_requirement
from agents.prompts import INTERVIEWER_V3
from agents.tools import INTERVIEWER_TOOL_SPECS, consult_interviewer_tools
from domain.claims import probe_from_key, unused_probes
from domain.enums import InterviewerAction
from domain.errors import InvalidAgentOutput
from domain.policies import wrap_untrusted
from domain.questions import materialize_question_decision, validate_question_binding
from domain.requirements import RUBRIC_EVIDENCE_ID, requirement_by_id, requirement_items
from domain.schemas import QuestionDecision, QuestionProposal
from domain.text import redact_identity
from llm_gateway.factory import build_gateway
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext


class InterviewerState(TypedDict, total=False):
    snapshot: dict
    owner_id: str
    session_id: str
    tool_results: dict
    proposal: dict
    decision: dict
    tool_calls: int
    prompt_version: str


def _materials(snapshot: dict[str, Any]) -> tuple[str, str]:
    return snapshot.get("resume_plain") or "", snapshot.get("jd_plain") or ""


def _proposal_from_requirement(
    snapshot: dict[str, Any],
    requirement: dict[str, Any],
    *,
    action: InterviewerAction,
    summary: str,
    parent_turn_id: str | None = None,
) -> QuestionProposal:
    resume, jd = _materials(snapshot)
    req_id = str(requirement.get("id") or "req_01")
    if action is InterviewerAction.ASK:
        claim = next_claim(snapshot)
    else:
        claim = probe_from_key(snapshot, snapshot.get("last_probe_key"))
    if claim is None:
        return QuestionProposal(
            action=InterviewerAction.END,
            reason="AGENDA_COMPLETE",
            decision_summary="No valid question direction remains.",
        )
    return QuestionProposal(
        action=action,
        reason="NEW_DIRECTION" if action is InterviewerAction.ASK else "NEEDS_DETAIL",
        claim_id=claim["id"],
        probe_key=claim["probe_key"],
        topic_id=req_id,
        skill_ids=[req_id],
        question_text=compose_personalized_question(
            resume=resume,
            jd=jd,
            skill_ids=[req_id],
            action=action,
            last_answer=snapshot.get("last_answer"),
            requirement_text=requirement.get("text") or "",
            claim_text=(claim or {}).get("text") or "",
            probe_hint=(claim or {}).get("probe_hint") or "",
        ),
        parent_turn_id=parent_turn_id,
        rubric_template_id=RUBRIC_EVIDENCE_ID,
        difficulty="intermediate",
        decision_summary=summary,
    )


def fallback_from_requirements(snapshot: dict[str, Any]) -> QuestionProposal:
    if int(snapshot.get("remaining_question_budget", 1)) <= 0 or snapshot.get("remaining_seconds") == 0:
        return QuestionProposal(
            action=InterviewerAction.END,
            reason="BUDGET_EXHAUSTED",
            decision_summary="Question or time budget reached.",
        )
    chosen = next_requirement(snapshot) or requirement_by_id(
        snapshot, (snapshot.get("last_skill_ids") or [None])[0]
    )
    if chosen is None:
        chosen = next(iter(requirement_items(snapshot)), None)
    if not chosen or not unused_probes(snapshot):
        return QuestionProposal(
            action=InterviewerAction.END,
            reason="AGENDA_COMPLETE",
            decision_summary="No available question directions remain.",
        )
    return _proposal_from_requirement(
        snapshot,
        chosen,
        action=InterviewerAction.ASK,
        summary="Fallback: probe an uncovered job requirement using this resume.",
    )


def load_snapshot(state: InterviewerState) -> InterviewerState:
    snap = dict(state.get("snapshot") or {})
    resume = redact_identity(snap.get("resume_plain") or "")
    jd = redact_identity(snap.get("jd_plain") or "")
    snap["resume_text"] = wrap_untrusted("RESUME_DATA", resume)
    snap["jd_text"] = wrap_untrusted("JD_DATA", jd)
    if snap.get("last_answer"):
        snap["last_answer_wrapped"] = wrap_untrusted("ANSWER_DATA", snap["last_answer"])
    snap["interviewer_prompt_version"] = "interviewer_v3"
    return {"snapshot": snap, "prompt_version": "interviewer_v3"}


def consult_tools(state: InterviewerState) -> InterviewerState:
    snap = dict(state["snapshot"])
    results = consult_interviewer_tools(snap, owner_id=state["owner_id"])
    snap["tool_results"] = results
    return {"snapshot": snap, "tool_results": results, "tool_calls": len(results)}


def budget_stop_proposal(snapshot: dict[str, Any]) -> QuestionProposal | None:
    if int(snapshot.get("remaining_question_budget", 1)) <= 0 or snapshot.get("remaining_seconds") == 0:
        return QuestionProposal(
            action=InterviewerAction.END,
            reason="BUDGET_EXHAUSTED",
            decision_summary="Question or time budget reached.",
        )
    return None


async def decide(state: InterviewerState) -> InterviewerState:
    stop = budget_stop_proposal(state["snapshot"])
    if stop is not None:
        return {"proposal": stop.model_dump()}
    pack = build_interviewer_context(state["snapshot"])
    pack["tool_results"] = state.get("tool_results") or state["snapshot"].get("tool_results") or {}
    gateway = build_gateway()
    result = await gateway.generate_structured(
        role=AgentRole.INTERVIEWER,
        input_snapshot=AgentInputSnapshot(
            id=state["snapshot"].get("id", "snap"),
            role=AgentRole.INTERVIEWER,
            payload=pack,
        ),
        output_schema=QuestionDecision,
        tools=INTERVIEWER_TOOL_SPECS,
        budget=BudgetContext(owner_id=state["owner_id"], session_id=state.get("session_id")),
    )
    return {"decision": result.data}


def validate_proposal(state: InterviewerState) -> InterviewerState:
    snapshot = state["snapshot"]
    try:
        if "decision" in state:
            proposal = materialize_question_decision(state["decision"], snapshot)
        else:
            proposal = QuestionProposal.model_validate(state.get("proposal") or {})
            validate_question_binding(proposal, snapshot)
    except (ValueError, TypeError, InvalidAgentOutput):
        return {"proposal": fallback_from_requirements(snapshot).model_dump()}
    return {"proposal": proposal.model_dump()}


def interviewer_graph():
    graph = StateGraph(InterviewerState)
    graph.add_node("load_snapshot", load_snapshot)
    graph.add_node("consult_tools", consult_tools)
    graph.add_node("decide", decide)
    graph.add_node("validate_proposal", validate_proposal)
    graph.add_edge(START, "load_snapshot")
    graph.add_edge("load_snapshot", "consult_tools")
    graph.add_edge("consult_tools", "decide")
    graph.add_edge("decide", "validate_proposal")
    graph.add_edge("validate_proposal", END)
    return graph.compile()


assert INTERVIEWER_V3
