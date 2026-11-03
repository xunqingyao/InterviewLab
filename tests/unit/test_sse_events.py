from domain.events import PUBLIC_SSE_EVENTS, public_event_payload


def test_strict_evaluation_progress_has_no_score():
    payload = public_event_payload(
        "evaluation.progress",
        {"completed": 1, "turn_id": "t1", "level": 3, "assessment": {"score": 3}},
        mode="STRICT",
    )
    assert payload == {"completed": 1, "turn_id": "t1"}
    assert "level" not in payload
    assert "assessment" not in payload


def test_job_failed_only_exposes_code():
    payload = public_event_payload(
        "job.failed",
        {"code": "TimeoutError", "kind": "EVALUATE_TURN", "traceback": "secret"},
        mode="STRICT",
    )
    assert payload == {"code": "TimeoutError", "kind": "EVALUATE_TURN"}
    assert "traceback" not in payload


def test_whitelist_covers_interview_events():
    for name in ("question.ready", "answer.accepted", "report.ready", "resync.required", "job.failed"):
        assert name in PUBLIC_SSE_EVENTS
    assert "training.ready" not in PUBLIC_SSE_EVENTS
