from __future__ import annotations

from collections import defaultdict
from typing import Any

from domain.enums import EvidenceStatus, ReportIntegrity
from domain.training import independent_items, is_independent_hint


def aggregate_report(
    *,
    session_id: str,
    mode: str,
    topics: list[dict[str, Any]],
    assessments: list[dict[str, Any]],
    missing_turn_ids: list[str],
    cutoff_turn_no: int,
) -> dict[str, Any]:
    """Deterministic aggregation. LLMs may polish language later but cannot add numbers or citations."""
    by_skill: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, assessment in enumerate(assessments):
        for dimension in assessment.get("dimensions", []):
            by_skill[dimension["skill_id"]].append(
                {
                    "turn_id": assessment.get("turn_id"),
                    "direction_id": assessment.get("direction_id")
                    or assessment.get("turn_id")
                    or assessment.get("id")
                    or str(index),
                    "turn_no": assessment.get("turn_no", index),
                    "revision": assessment.get("revision", 1),
                    "assessment_id": assessment.get("id"),
                    "level": dimension.get("level"),
                    "evidence_status": dimension.get("evidence_status"),
                    "demonstrated": dimension.get("demonstrated"),
                    "missing": dimension.get("missing"),
                    "hint_level": dimension.get("hint_level") or "NONE",
                    "answer_evidence": dimension.get("answer_evidence", []),
                }
            )

    labels = {}
    for topic in topics:
        rid = topic.get("requirement_id") or topic.get("skill_id")
        if rid:
            labels[str(rid)] = topic.get("text") or str(rid)
    coverage = []
    for skill_id in sorted(set(labels) | set(by_skill)):
        history = by_skill.get(skill_id, [])
        latest = {}
        for item in history:
            current = latest.get(item["direction_id"])
            if current is None or (item["turn_no"], item["revision"]) >= (
                current["turn_no"],
                current["revision"],
            ):
                latest[item["direction_id"]] = item
        items = list(latest.values())
        numeric = [i["level"] for i in items if i["level"] is not None]
        independent = independent_items(items)
        coverage.append(
            {
                "skill_id": skill_id,
                "requirement_id": skill_id,
                "label": labels.get(skill_id, skill_id),
                "sample_count": len(items),
                "observation_count": len(history),
                "independent_sample_count": len(independent),
                "null_count": sum(1 for i in items if i["level"] is None),
                "levels": numeric,
                "evidence": items,
                "history": history,
                "assessment_status": "ASSESSED" if items else "NOT_ASSESSED",
            }
        )

    issues = []
    for coverage_item in coverage:
        for dimension in coverage_item["evidence"]:
            status = dimension.get("evidence_status")
            if status in {
                EvidenceStatus.MISSING,
                EvidenceStatus.INSUFFICIENT_EVIDENCE,
                EvidenceStatus.REFERENCE_CONFLICT,
            }:
                issues.append(
                    {
                        "skill_id": coverage_item["skill_id"],
                        "kind": status,
                        "detail": dimension.get("missing") or dimension.get("demonstrated"),
                        "turn_id": dimension.get("turn_id"),
                    }
                )
        effective_ids = {row["assessment_id"] for row in coverage_item["evidence"]}
        for gap in [
            gap
            for assessment in assessments
            if assessment.get("id") in effective_ids
            for gap in assessment.get("verification_gaps", [])
            if gap.get("skill_id") == coverage_item["skill_id"]
        ]:
            issues.append(
                {
                    "skill_id": gap["skill_id"],
                    "kind": "VERIFICATION_GAP",
                    "detail": gap["description"],
                    "turn_id": next((row["turn_id"] for row in coverage_item["evidence"]), None),
                }
            )

    next_training = [item["skill_id"] for item in coverage if _needs_training(item)][:3]
    integrity = ReportIntegrity.PARTIAL if missing_turn_ids else ReportIntegrity.COMPLETE
    return {
        "session_id": session_id,
        "mode": mode,
        "cutoff_turn_no": cutoff_turn_no,
        "topics": topics,
        "effective_assessment_count": len(assessments),
        "missing_turn_ids": missing_turn_ids,
        "coverage": coverage,
        "issues": issues,
        "next_training": next_training,
        "next_assessment": [item["skill_id"] for item in coverage if not item["sample_count"]],
        "integrity": integrity,
        "limitations": [
            "Levels are training rubrics, not hiring scores.",
            "Null levels are excluded from numeric lists and counted separately.",
            "Hinted answers are not mixed into independent mastery.",
        ],
    }


def _needs_training(item: dict[str, Any]) -> bool:
    if not item.get("sample_count"):
        return False
    independent = independent_items(item.get("evidence") or [])
    independent_levels = [row["level"] for row in independent if row.get("level") is not None]
    if not independent_levels:
        return True
    if max(independent_levels) <= 1:
        return True
    return any(
        e.get("evidence_status") in {EvidenceStatus.PARTIAL, EvidenceStatus.MISSING}
        for e in independent
        if is_independent_hint(e.get("hint_level"))
    )
