from agents.personalize import compose_training_pair
from domain.training import (
    exercise_key,
    is_independent_hint,
    pick_hint_pair,
    requirement_id_from_key,
    resolve_training_targets,
    retest_key,
)


def test_hinted_levels_are_not_independent():
    assert is_independent_hint("NONE")
    assert is_independent_hint(None)
    assert not is_independent_hint("NUDGE")
    assert not is_independent_hint("STRUCTURE")
    assert not is_independent_hint("EXAMPLE")


def test_retest_question_and_key_differ_from_exercise():
    claim = {
        "id": "claim_01",
        "text": "主导过 FastAPI 任务队列",
        "probe_hints": ["约束与取舍", "如何验证"],
    }
    exercise_hint, retest_hint = pick_hint_pair(claim)
    exercise, retest = compose_training_pair(
        requirement_text="幂等",
        claim_text=claim["text"],
        exercise_hint=exercise_hint,
        retest_hint=retest_hint,
    )
    assert exercise
    assert retest
    assert exercise != retest
    assert exercise_key("req_01") != retest_key("req_01")
    assert requirement_id_from_key(exercise_key("req_01")) == "req_01"
    assert requirement_id_from_key(retest_key("req_01")) == "req_01"


def test_coach_ids_resolve_to_session_requirements_not_catalog():
    snapshot = {
        "requirements": {"items": [{"id": "req_01", "text": "幂等"}, {"id": "req_02", "text": "恢复"}]},
        "weak_skills": ["req_01"],
    }
    picked = resolve_training_targets(["rag.evaluation_design", "ex:req_02"], snapshot)
    assert [item["id"] for item in picked] == ["req_01"]
    fallback = resolve_training_targets(["catalog.only"], snapshot)
    assert fallback[0]["id"] == "req_01"
