"""Freeze this job's requirements on the server. The client cannot supply them."""

from __future__ import annotations

import re
from typing import Any

from domain.rubrics import RUBRIC_POLICY_ID
from domain.text import normalize_text, redact_identity

GENERIC_PROBE = "说明这项技术如何解决本题问题，以及关键步骤的作用"
RUBRIC_EVIDENCE_ID = RUBRIC_POLICY_ID

_MUST = ("必须", "必备", "需要", "要求", "熟悉", "精通", "掌握", "负责", "required")
_NICE = ("加分", "优先", "最好", "nice")


def _priority(chunk: str) -> str:
    if any(token in chunk for token in _NICE) and not any(
        token in chunk for token in ("必须", "必备", "required")
    ):
        return "nice"
    return "must"


def _is_title_line(chunk: str) -> bool:
    if any(token in chunk for token in _MUST + _NICE):
        return False
    return bool(re.search(r"岗位名称|职位名称|薪资|福利|招聘人数", chunk)) or (
        "/" in chunk and len(chunk) < 24 and not re.search(r"能力|经验|设计|开发|处理|理解", chunk)
    )


def extract_requirements(text: str) -> dict[str, Any]:
    """Parse JD text into frozen requirement items. Always server-owned."""
    raw = redact_identity(normalize_text(text or "")).strip()
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    blocks = [part.strip() for part in re.split(r"[\n；;。]+", raw) if part.strip()]
    if not blocks and raw:
        blocks = [raw]
    for block in blocks:
        if _is_title_line(block):
            continue
        priority = _priority(block)
        # Conservative fallback: retain a complete clause, including conjunctions.
        part = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", block).strip()
        key = re.sub(r"\s+", "", part)
        if key in seen or len(part) < 2:
            continue
        seen.add(key)
        items.append(
            {
                "id": f"req_{len(items) + 1:02d}",
                "priority": priority,
                "text": part[:600],
                "probe_angle": GENERIC_PROBE,
            }
        )
        if len(items) >= 8:
            break
    if not items and raw:
        items.append({"id": "req_01", "priority": "must", "text": raw[:120], "probe_angle": GENERIC_PROBE})
    return {"items": items[:8], "source": "backend"}


def ground_extracted_requirements(text: str, rows: list[Any]) -> list[dict[str, Any]]:
    original = redact_identity(normalize_text(text))
    items = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote = normalize_text(row.get("text") or "").strip()
        key = re.sub(r"\s+", "", quote)
        if len(quote) < 2 or quote not in original or key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "id": f"req_{len(items) + 1:02d}",
                "text": quote[:600],
                "priority": row.get("priority") or "must",
                "probe_angle": GENERIC_PROBE,
            }
        )
        if len(items) >= 8:
            break
    return items


def coerce_requirements(blob: Any, text: str = "") -> dict[str, Any]:
    if isinstance(blob, dict) and blob.get("items"):
        return blob
    return extract_requirements(text)


def requirement_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blob = payload.get("requirements")
    if isinstance(blob, dict) and blob.get("items"):
        return list(blob["items"])
    if isinstance(blob, list) and blob:
        return list(blob)
    return extract_requirements(payload.get("jd_plain") or payload.get("jd_text") or "").get("items") or []


def unused_requirements(payload: dict[str, Any]) -> list[dict[str, Any]]:
    used = set(payload.get("used_requirement_ids") or [])
    used.update(payload.get("covered_skill_ids") or [])
    return [item for item in requirement_items(payload) if item.get("id") not in used]


def pick_requirement(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    must = [item for item in items if item.get("priority") == "must"]
    return (must or items)[0]


def requirement_by_id(payload: dict[str, Any], req_id: str | None) -> dict[str, Any] | None:
    if not req_id:
        return None
    for item in requirement_items(payload):
        if item.get("id") == req_id:
            return item
    return None
