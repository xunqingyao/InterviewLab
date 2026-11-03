"""Read-only Interviewer tools. They only see the current session snapshot."""

from __future__ import annotations

from typing import Any

from domain.claims import unused_probes
from domain.policies import MAX_INTERVIEWER_TOOLS
from domain.requirements import requirement_by_id, unused_requirements
from llm_gateway.interface import ToolSpec

INTERVIEWER_TOOL_SPECS = [
    ToolSpec(
        name="get_resume_claims",
        description="Return unused frozen resume claims (id + original text). Not the full resume.",
        parameters={"type": "object", "properties": {"max_items": {"type": "integer"}}, "additionalProperties": False},
    ),
    ToolSpec(
        name="get_turn_evidence",
        description="Return the latest published question and submitted answer in this session.",
        parameters={"type": "object", "properties": {"max_chars": {"type": "integer"}}, "additionalProperties": False},
    ),
    ToolSpec(
        name="get_agenda_status",
        description="Return covered and unused job requirements, plus question budget.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="list_job_requirements",
        description="Return unused and current frozen JD requirements for this session. Not a question bank.",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    ),
]


def get_resume_claims(snapshot: dict[str, Any], *, max_items: int = 8) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in unused_probes(snapshot):
        claim_id = str(item.get("id") or "")
        row = grouped.setdefault(
            claim_id,
            {
                "id": claim_id,
                "project": item.get("project") or "",
                "kind": item.get("kind"),
                "text": item.get("text"),
                "available_probes": [],
            },
        )
        if item.get("probe_hint"):
            row["available_probes"].append({"probe_key": item["probe_key"], "probe_hint": item["probe_hint"]})
    return {"claims": list(grouped.values())[:max_items]}


def get_turn_evidence(snapshot: dict[str, Any], *, max_chars: int = 800) -> dict[str, Any]:
    answer = (snapshot.get("last_answer") or "")[:max_chars]
    question = (snapshot.get("last_question") or "")[:max_chars]
    return {
        "turn_id": snapshot.get("last_turn_id"),
        "question_text": question,
        "answer_text": answer,
        "followup_depth": int(snapshot.get("followup_depth") or 0),
        "topic_id": snapshot.get("last_topic_id"),
        "skill_ids": snapshot.get("last_skill_ids") or [],
        "rubric_template_id": snapshot.get("last_rubric_template_id"),
        "template_key": snapshot.get("last_template_key"),
        "difficulty": snapshot.get("last_difficulty") or "intermediate",
    }


def get_agenda_status(snapshot: dict[str, Any]) -> dict[str, Any]:
    unused = unused_requirements(snapshot)
    covered = list(snapshot.get("used_requirement_ids") or snapshot.get("covered_skill_ids") or [])
    return {
        "answered_count": int(snapshot.get("answered_count") or 0),
        "remaining_question_budget": int(snapshot.get("remaining_question_budget") or 0),
        "max_questions": int(snapshot.get("max_questions") or 8),
        "followup_depth": int(snapshot.get("followup_depth") or 0),
        "covered_requirement_ids": covered,
        "unused_requirement_ids": [item.get("id") for item in unused],
        "remaining_seconds": snapshot.get("remaining_seconds"),
        "mode": snapshot.get("mode") or "STRICT",
    }


def list_job_requirements(snapshot: dict[str, Any]) -> dict[str, Any]:
    unused = unused_requirements(snapshot)
    current_id = (snapshot.get("last_skill_ids") or [None])[0]
    current = requirement_by_id(snapshot, current_id)
    return {
        "unused": [
            {"id": item.get("id"), "priority": item.get("priority"), "text": item.get("text")}
            for item in unused
        ],
        "current": {"id": current.get("id"), "text": current.get("text")} if current else None,
        "covered_ids": list(snapshot.get("used_requirement_ids") or snapshot.get("covered_skill_ids") or []),
    }


def consult_interviewer_tools(snapshot: dict[str, Any], *, owner_id: str) -> dict[str, Any]:
    """Run at most MAX_INTERVIEWER_TOOLS read-only lookups. owner_id is injected, never from the model."""
    _ = owner_id
    results: dict[str, Any] = {}
    remaining = MAX_INTERVIEWER_TOOLS
    results["get_agenda_status"] = get_agenda_status(snapshot)
    remaining -= 1
    if remaining <= 0:
        return results
    if snapshot.get("last_answer"):
        results["get_turn_evidence"] = get_turn_evidence(snapshot)
    else:
        results["get_resume_claims"] = get_resume_claims(snapshot)
    remaining -= 1
    if remaining <= 0:
        return results
    results["list_job_requirements"] = list_job_requirements(snapshot)
    return results
