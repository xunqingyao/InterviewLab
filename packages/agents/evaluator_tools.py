"""Read-only Evaluator tools. They only see the frozen turn snapshot, never future turns."""

from __future__ import annotations

from typing import Any

from domain.policies import MAX_EVALUATOR_TOOLS
from llm_gateway.interface import ToolSpec

EVALUATOR_TOOL_SPECS = [
    ToolSpec(
        name="get_question_rubric",
        description="Return the frozen rubric snapshot for this turn. Cannot swap standards.",
        parameters={
            "type": "object",
            "properties": {"rubric_snapshot_id": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="read_transcript",
        description="Return Q/A text up to the cutoff turn. Future turns are invisible.",
        parameters={
            "type": "object",
            "properties": {"max_chars": {"type": "integer"}},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="search_reference",
        description="Search frozen knowledge chunks for this session snapshot only.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_reference_chunk",
        description="Return one chunk if it belongs to this session knowledge snapshot.",
        parameters={
            "type": "object",
            "properties": {"chunk_id": {"type": "string"}},
            "required": ["chunk_id"],
            "additionalProperties": False,
        },
    ),
]


def get_question_rubric(snapshot: dict[str, Any], rubric_snapshot_id: str | None = None) -> dict[str, Any]:
    expected = snapshot.get("rubric_snapshot_id")
    if rubric_snapshot_id and expected and rubric_snapshot_id != expected:
        return {"error": "rubric snapshot mismatch; frozen snapshot cannot be replaced"}
    return {
        "rubric_snapshot_id": expected,
        "criteria": snapshot.get("rubric_criteria") or {},
        "question_text": snapshot.get("question_text") or "",
    }


def read_transcript(snapshot: dict[str, Any], *, max_chars: int = 4000) -> dict[str, Any]:
    cutoff = int(snapshot.get("cutoff_turn_no") or 0)
    items = []
    used = 0
    chain_turns = {
        int(row["turn_no"]) for row in snapshot.get("thread") or [] if row.get("turn_no") is not None
    }
    for row in snapshot.get("transcript") or []:
        if cutoff and int(row.get("turn_no") or 0) > cutoff:
            continue
        if chain_turns and int(row.get("turn_no") or 0) not in chain_turns:
            continue
        text = (row.get("text") or "")[: max(0, max_chars - used)]
        items.append(
            {
                "turn_no": row.get("turn_no"),
                "kind": row.get("kind"),
                "message_id": row.get("message_id"),
                "text": text,
            }
        )
        used += len(text)
        if used >= max_chars:
            break
    return {"cutoff_turn_no": cutoff, "messages": items}


def search_reference(snapshot: dict[str, Any], query: str | None = None) -> list[dict[str, Any]]:
    hits = list(snapshot.get("reference_hits") or [])
    needle = (query or "").strip()
    if needle:
        hits = [h for h in hits if needle in (h.get("text") or "")] or hits[:3]
    return [
        {"id": h.get("id"), "text": (h.get("text") or "")[:500], "skill_tags": h.get("skill_tags") or []}
        for h in hits[:5]
    ]


def get_reference_chunk(snapshot: dict[str, Any], chunk_id: str) -> dict[str, Any]:
    allowed = {h.get("id") for h in snapshot.get("reference_hits") or []}
    if chunk_id not in allowed:
        return {"error": "chunk is not in this session knowledge snapshot"}
    for hit in snapshot.get("reference_hits") or []:
        if hit.get("id") == chunk_id:
            return {"id": chunk_id, "text": hit.get("text"), "skill_tags": hit.get("skill_tags") or []}
    return {"error": "chunk not found"}


def consult_evaluator_tools(snapshot: dict[str, Any], *, owner_id: str) -> dict[str, Any]:
    _ = owner_id
    results: dict[str, Any] = {}
    remaining = MAX_EVALUATOR_TOOLS
    results["get_question_rubric"] = get_question_rubric(snapshot)
    remaining -= 1
    results["read_transcript"] = read_transcript(snapshot)
    remaining -= 1
    if remaining <= 0:
        return results
    results["search_reference"] = search_reference(snapshot, snapshot.get("answer_text"))
    remaining -= 1
    hits = results["search_reference"]
    if remaining and hits:
        results["get_reference_chunk"] = get_reference_chunk(snapshot, hits[0]["id"])
    return results
