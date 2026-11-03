from __future__ import annotations

from domain.enums import EventType, InterviewMode

PUBLIC_SSE_EVENTS = frozenset(
    {
        EventType.SESSION_STARTED.value,
        EventType.PHASE_CHANGED.value,
        EventType.ANSWER_ACCEPTED.value,
        EventType.QUESTION_READY.value,
        EventType.EVALUATION_PROGRESS.value,
        EventType.REPORT_READY.value,
        EventType.SESSION_COMPLETED.value,
        EventType.SESSION_CANCELLED.value,
        EventType.JOB_FAILED.value,
        EventType.RESYNC_REQUIRED.value,
        EventType.AUDIO_TRANSCRIBED.value,
    }
)


def public_event_payload(event_type: str, payload: dict, *, mode: str) -> dict:
    """Strip private scores/tools before anything is sent on SSE."""
    data = dict(payload or {})
    if event_type == EventType.EVALUATION_PROGRESS.value and mode == InterviewMode.STRICT.value:
        return {key: data[key] for key in ("completed", "turn_id", "session_id") if key in data}
    if event_type == EventType.JOB_FAILED.value:
        return {key: data[key] for key in ("code", "kind", "session_id") if key in data}
    data.pop("rubric", None)
    data.pop("criteria", None)
    data.pop("result_json", None)
    data.pop("assessment", None)
    return data
