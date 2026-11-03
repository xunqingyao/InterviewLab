"""Coach writes proposals only. Domain turns them into one exercise + one retest."""

from __future__ import annotations

from typing import Any

from domain.claims import claim_items
from domain.enums import HintLevel
from domain.schemas import CoachProposal

LAYERED_HINTS = [
    {"level": HintLevel.NUDGE.value, "text": "先写出这条岗位要求下的约束、指标和对照条件。"},
    {
        "level": HintLevel.STRUCTURE.value,
        "text": "按背景 / 方案 / 验证 / 失败 组织，并点名哪一部分是你亲自做的。",
    },
    {
        "level": HintLevel.EXAMPLE.value,
        "text": "示范：同一测试集、同一生成配置，只改一个变量，并说明失败时你怎么发现。照着写不算独立掌握。",
    },
]


class TrainingProposalError(ValueError):
    """Safe, public reason: never include resume text or raw model output."""

    def __init__(self, reason_code: str, message: str):
        self.reason_code = reason_code
        self.public_message = {
            "EMPTY_ANGLE": "训练角度不能为空。",
            "INVALID_TARGET": "训练目标必须来自报告待训练要求，且不能重复。",
            "INVALID_CLAIM": "训练引用的简历陈述不属于本场简历。",
            "DUPLICATE_ANGLE": "练习和变式复测必须使用不同角度。",
        }.get(reason_code, "训练提案未通过校验。")
        super().__init__(message)


def hint_value(hint_level: Any) -> str:
    if hasattr(hint_level, "value"):
        return str(hint_level.value)
    raw = str(hint_level or HintLevel.NONE.value).strip() or HintLevel.NONE.value
    return raw


def is_independent_hint(hint_level: Any) -> bool:
    """Only NONE counts as independent mastery. NUDGE / STRUCTURE / EXAMPLE do not."""
    return hint_value(hint_level) == HintLevel.NONE.value


def independent_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if is_independent_hint(item.get("hint_level"))]


def exercise_key(requirement_id: str) -> str:
    return f"ex:{requirement_id}"


def retest_key(requirement_id: str) -> str:
    return f"retest:{requirement_id}"


def requirement_id_from_key(template_key: str | None) -> str:
    raw = str(template_key or "").strip()
    if ":" in raw:
        return raw.split(":")[-1]
    return raw


def pick_hint_pair(claim: dict[str, Any] | None) -> tuple[str, str]:
    hints = [str(item).strip() for item in (claim or {}).get("probe_hints") or [] if str(item).strip()]
    if len(hints) >= 2:
        return hints[0][:40], hints[1][:40]
    if hints:
        return hints[0][:40], "换一个可比较的失败或边界案例"
    return "约束与取舍", "如何验证"


def resolve_training_targets(proposal_ids: list[str], snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Map Coach ids onto this session's frozen requirements. Catalog skill names are ignored."""
    items = []
    blob = snapshot.get("requirements") or {}
    if isinstance(blob, dict):
        items = list(blob.get("items") or [])
    elif isinstance(blob, list):
        items = list(blob)
    weak_ids = set(snapshot.get("weak_skills") or []) | {
        item["id"] for item in snapshot.get("weak_requirements") or [] if isinstance(item, dict)
    }
    by_id = {
        str(item.get("id")): item for item in items if isinstance(item, dict) and item.get("id") in weak_ids
    }
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _take(raw: Any) -> None:
        rid = requirement_id_from_key(str(raw))
        row = by_id.get(str(raw)) or by_id.get(rid)
        if row and str(row["id"]) not in seen:
            picked.append(row)
            seen.add(str(row["id"]))

    for raw in proposal_ids:
        _take(raw)
        if len(picked) >= 3:
            return picked
    if picked:
        return picked
    for raw in snapshot.get("weak_skills") or []:
        _take(raw)
        if len(picked) >= 3:
            return picked
    if picked:
        return picked
    return []


def validate_training_proposal(proposal: CoachProposal, snapshot: dict[str, Any]) -> None:
    weak_ids = set(snapshot.get("weak_skills") or []) | {
        row["id"] for row in snapshot.get("weak_requirements") or [] if isinstance(row, dict)
    }
    claims = {row["id"] for row in claim_items(snapshot)}
    seen: set[str] = set()
    for target in proposal.targets:
        if not target.exercise_hint.strip() or not target.retest_hint.strip():
            raise TrainingProposalError("EMPTY_ANGLE", "training angles cannot be blank")
        if target.requirement_id not in weak_ids or target.requirement_id in seen:
            raise TrainingProposalError("INVALID_TARGET", "training targets must be distinct confirmed weak requirements")
        if target.claim_id is not None and target.claim_id not in claims:
            raise TrainingProposalError("INVALID_CLAIM", "training claim must belong to this resume")
        if target.exercise_hint.strip() == target.retest_hint.strip():
            raise TrainingProposalError("DUPLICATE_ANGLE", "retest must use a different angle")
        seen.add(target.requirement_id)


def relevant_claim(requirement_text: str, claims: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Conservative deterministic selection for the offline gateway; no match means no project."""
    import re

    tokens = set(re.findall(r"[A-Za-z][A-Za-z0-9_]+", requirement_text.lower()))
    chinese = re.findall(r"[\u4e00-\u9fff]+", requirement_text)
    tokens.update(part[index : index + 2] for part in chinese for index in range(len(part) - 1))
    tokens -= {"必须", "熟悉", "掌握", "经验", "能力", "要求", "具备"}
    scored = [(sum(token in row.get("text", "").lower() for token in tokens), row) for row in claims]
    best = max(scored, key=lambda item: item[0], default=(0, None))
    return best[1] if best[0] else None
