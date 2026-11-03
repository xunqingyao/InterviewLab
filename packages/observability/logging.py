from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import structlog


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_obj(value: Any) -> str:
    return hash_text(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False))


_BLOCKED_KEYS = {
    "api_key",
    "token",
    "csrf",
    "csrf_token",
    "password",
    "authorization",
    "answer_text",
    "resume_text",
    "transcript",
    "normalized_text",
}


def redact(event: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in event.items():
        lowered = key.lower()
        if lowered in _BLOCKED_KEYS or "secret" in lowered or "token" in lowered:
            out[key] = "[redacted]"
        elif isinstance(value, dict):
            out[key] = redact(value)
        elif isinstance(value, str) and len(value) > 400:
            out[key] = value[:80] + "…"
        else:
            out[key] = value
    return out


logger = logging.getLogger("interviewlab")


def log_event(event: str, **fields: Any) -> None:
    logger.info("%s %s", event, json.dumps(redact(fields), ensure_ascii=False, default=str))
