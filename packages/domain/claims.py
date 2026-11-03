"""Freeze probeable resume claims on the server. These are source quotes, not a syllabus."""

from __future__ import annotations

import re
from typing import Any

from domain.text import normalize_text, redact_identity

_PROJECT = ("项目", "系统", "平台", "负责", "主导")
_STACK = ("FastAPI", "PostgreSQL", "Python", "RAG", "队列", "Redis", "LangGraph", "Agent")
DEFAULT_PROBE_HINTS = ("约束与取舍", "如何验证", "失败与边界", "亲历范围", "和岗位要求如何对应")


def _kind(line: str) -> str:
    if any(ch.isdigit() for ch in line):
        return "metric"
    if any(token in line for token in _PROJECT):
        return "project"
    if any(token in line for token in _STACK):
        return "stack"
    return "duty"


def _plain_lines(text: str) -> list[str]:
    lines = []
    for raw in redact_identity(normalize_text(text or "")).splitlines():
        line = raw.strip()
        if not line or "DATA_ONLY" in line or line.startswith("<"):
            continue
        line = re.sub(r"\[REDACTED_[A-Z]+\]", "", line)
        line = re.sub(r"[，,]\s*$", "", line).strip(" ，,")
        if len(line) < 8:
            continue
        lines.append(line[:240])
    return lines


def _claim_row(
    claim_id: str,
    text: str,
    *,
    kind: str = "duty",
    project: str = "",
    probe_hints: list[str] | None = None,
) -> dict[str, Any]:
    hints = [str(item).strip()[:40] for item in (probe_hints or []) if str(item).strip()][:5]
    return {
        "id": claim_id,
        "project": (project or text[:40]).strip()[:80],
        "kind": kind or "duty",
        "text": text[:240],
        "probe_hints": hints or list(DEFAULT_PROBE_HINTS),
    }


def _find_verbatim(resume: str, quote: str) -> str | None:
    haystack = redact_identity(normalize_text(resume or ""))
    snippet = re.sub(r"\[REDACTED_[A-Z]+\]", "", normalize_text(quote or "")).strip()
    if len(snippet) < 8:
        return None
    if snippet in haystack:
        return snippet[:240]
    prefix = snippet[:40]
    if prefix and prefix in haystack:
        start = haystack.find(prefix)
        if haystack.find(prefix, start + 1) != -1:
            return None
        return haystack[start : start + 240].splitlines()[0][:240]
    return None


def ground_extracted_claims(resume: str, raw_claims: list[Any]) -> list[dict[str, Any]]:
    """Keep only model claims whose text appears in the resume. Invented wording is dropped."""
    items = []
    seen: set[str] = set()
    for row in raw_claims:
        if not isinstance(row, dict):
            continue
        grounded = _find_verbatim(resume, row.get("text") or "")
        if not grounded:
            continue
        key = re.sub(r"\s+", "", grounded)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            _claim_row(
                f"claim_{len(items) + 1:02d}",
                grounded,
                kind=row.get("kind") or _kind(grounded),
                project=row.get("project") or "",
                probe_hints=row.get("probe_hints"),
            )
        )
        if len(items) >= 12:
            break
    return items


def extract_claims(text: str) -> dict[str, Any]:
    """Parse resume text into frozen claim items. Always server-owned."""
    lines = _plain_lines(text)
    preferred = [
        line
        for line in lines
        if any(token in line for token in _PROJECT + _STACK) or any(ch.isdigit() for ch in line)
    ]
    chosen = preferred or lines
    items = []
    seen: set[str] = set()
    for line in chosen:
        key = re.sub(r"\s+", "", line)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "id": f"claim_{len(items) + 1:02d}",
                "project": line[:40],
                "kind": _kind(line),
                "text": line,
                "probe_hints": list(DEFAULT_PROBE_HINTS),
            }
        )
        if len(items) >= 10:
            break
    if not items and (text or "").strip():
        collapsed = " ".join(redact_identity(text).split())[:240]
        if collapsed:
            items.append(
                {
                    "id": "claim_01",
                    "project": collapsed[:40],
                    "kind": "duty",
                    "text": collapsed,
                    "probe_hints": list(DEFAULT_PROBE_HINTS),
                }
            )
    return {"items": items, "source": "backend"}


def _normalize_list(raw: list[Any]) -> list[dict[str, Any]]:
    items = []
    for index, row in enumerate(raw):
        if isinstance(row, str) and row.strip():
            items.append(_claim_row(f"claim_{len(items) + 1:02d}", row.strip(), kind=_kind(row)))
            continue
        if not isinstance(row, dict):
            continue
        text = (row.get("text") or row.get("raw") or "").strip()
        if not text:
            continue
        items.append(
            _claim_row(
                row.get("id") or f"claim_{len(items) + 1:02d}",
                text,
                kind=row.get("kind") or _kind(text),
                project=row.get("project") or "",
                probe_hints=row.get("probe_hints"),
            )
        )
    return items


def coerce_claims(blob: Any, text: str = "") -> dict[str, Any]:
    """Read a claims envelope, a saved resume's nested claims, or a legacy list."""
    candidates = [blob]
    if isinstance(blob, dict):
        candidates.append(blob.get("claims"))
    for candidate in candidates:
        source = "backend"
        if isinstance(candidate, dict):
            raw = candidate.get("items")
            source = candidate.get("source") or source
        else:
            raw = candidate
        # Iterating a dict returns field names, not claim rows. Strings also are not lists.
        if not isinstance(raw, list):
            continue
        items = _normalize_list(raw)
        if items:
            return {"items": items, "source": source}
    return extract_claims(text)


def claim_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    blob = payload.get("claims")
    if isinstance(blob, dict) and (blob.get("items") or blob.get("unused") is not None):
        if blob.get("items"):
            return list(blob["items"])
        rows = list(blob.get("unused") or [])
        current = blob.get("current")
        if current:
            rows = [current] + rows
        return rows
    if isinstance(blob, list) and blob:
        return list(blob)
    structured = payload.get("structured_json") or {}
    return coerce_claims(structured, payload.get("resume_plain") or payload.get("resume_text") or "").get("items") or []


def unused_claims(payload: dict[str, Any]) -> list[dict[str, Any]]:
    used = set(payload.get("used_claim_ids") or [])
    return [item for item in claim_items(payload) if item.get("id") not in used]


def unused_probes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    used_keys = set(payload.get("used_probe_keys") or [])
    used_claims = set(payload.get("used_claim_ids") or [])
    out = []
    seen: set[str] = set()
    for claim in claim_items(payload):
        hints = list(claim.get("probe_hints") or DEFAULT_PROBE_HINTS)
        claim_id = str(claim.get("id") or "")
        if claim_id in used_claims and not any(key.startswith(f"{claim_id}:") for key in used_keys):
            continue
        probes = claim.get("available_probes")
        if probes is None:
            probes = [{"probe_key": f"{claim_id}:{index}", "probe_hint": hint} for index, hint in enumerate(hints)]
        for probe in probes:
            key = probe["probe_key"]
            if key in used_keys or key in seen:
                continue
            seen.add(key)
            out.append({**claim, **probe})
    return out


def pick_claim(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    preferred = [item for item in items if item.get("kind") in {"project", "metric", "stack"}]
    return (preferred or items)[0]


def pick_probe(payload: dict[str, Any]) -> dict[str, Any] | None:
    return (unused_probes(payload) or [None])[0]


def claim_by_id(payload: dict[str, Any], claim_id: str | None) -> dict[str, Any] | None:
    if not claim_id:
        return None
    for item in claim_items(payload):
        if item.get("id") == claim_id:
            return item
    return None


def used_claim_ids_from_questions(claims: list[dict[str, Any]], question_texts: list[str]) -> list[str]:
    used = []
    for item in claims:
        text = item.get("text") or ""
        if not text:
            continue
        needle = text[:40]
        if any(text in (question or "") or needle in (question or "") for question in question_texts):
            used.append(str(item["id"]))
    return used


def match_claim_id(claims: list[dict[str, Any]], question_text: str | None) -> str | None:
    found = used_claim_ids_from_questions(claims, [question_text or ""])
    return found[0] if found else None


def used_probe_keys_from_questions(claims: list[dict[str, Any]], question_texts: list[str]) -> list[str]:
    used = []
    for item in claims:
        hints = list(item.get("probe_hints") or [])
        claim_id = str(item.get("id") or "")
        if not hints:
            if claim_id in used_claim_ids_from_questions([item], question_texts):
                used.append(f"{claim_id}:0")
            continue
        for index, hint in enumerate(hints):
            if hint and any(hint in (question or "") for question in question_texts):
                used.append(f"{claim_id}:{index}")
    return used


def match_probe_key(claims: list[dict[str, Any]], question_text: str | None) -> str | None:
    found = used_probe_keys_from_questions(claims, [question_text or ""])
    return found[0] if found else None


def probe_from_key(payload: dict[str, Any], probe_key: str | None) -> dict[str, Any] | None:
    if not probe_key or ":" not in str(probe_key):
        return None
    claim_id, _, index_text = str(probe_key).rpartition(":")
    claim = claim_by_id(payload, claim_id)
    if not claim:
        return None
    if claim.get("available_probes") is not None:
        probe = next((row for row in claim["available_probes"] if row["probe_key"] == probe_key), None)
        return {**claim, **probe} if probe else None
    hints = list(claim.get("probe_hints") or DEFAULT_PROBE_HINTS)
    try:
        index = int(index_text)
        if index < 0 or str(index) != index_text:
            return None
        hint = hints[index]
    except (ValueError, IndexError):
        return None
    return {**claim, "probe_hint": hint, "probe_key": probe_key}
