"""Hide synthetic case-name grade suffixes in provider-visible IDs, preserving source labels."""

from __future__ import annotations

import hashlib
from typing import Any

TEXT_FIELDS = {
    "text",
    "answer_text",
    "question_text",
    "answer",
    "question",
    "quote",
    "description",
    "demonstrated",
    "missing",
    "decision_summary",
    "reason",
}


def identifier_map(snapshot: dict) -> dict[str, str]:
    values = {snapshot.get(k) for k in ("id", "turn_id", "rubric_snapshot_id", "answer_message_id")}
    for row in snapshot.get("thread", []):
        values.update(row.get(k) for k in ("turn_id", "answer_message_id"))
    values.update(row.get("message_id") for row in snapshot.get("transcript", []))
    return {
        value: "opaque_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]
        for value in values
        if isinstance(value, str) and value
    }


def replace_ids(value: Any, mapping: dict[str, str]) -> Any:
    """Only exact identifiers are replaced; answer, question and rubric text remain verbatim."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_ids(item, mapping) for item in value]
    if isinstance(value, dict):
        return {
            mapping.get(key, key): (
                item if key in TEXT_FIELDS and isinstance(item, str) else replace_ids(item, mapping)
            )
            for key, item in value.items()
        }
    return value


def restore_ids(value: Any, snapshot: dict) -> Any:
    return replace_ids(value, {opaque: original for original, opaque in identifier_map(snapshot).items()})
