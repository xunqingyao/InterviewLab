from domain.enums import EvidenceStatus, HintLevel
from domain.reports import aggregate_report


def test_null_levels_are_counted_not_averaged():
    body = aggregate_report(
        session_id="s",
        mode="STRICT",
        topics=[],
        assessments=[
            {
                "id": "a1",
                "turn_id": "t1",
                "dimensions": [
                    {
                        "skill_id": "rag.evaluation_design",
                        "level": None,
                        "evidence_status": EvidenceStatus.INSUFFICIENT_EVIDENCE,
                        "demonstrated": "",
                        "missing": "no eval set",
                        "hint_level": HintLevel.NONE,
                    }
                ],
                "verification_gaps": [],
            }
        ],
        missing_turn_ids=[],
        cutoff_turn_no=1,
    )
    skill = body["coverage"][0]
    assert skill["null_count"] == 1
    assert skill["levels"] == []
    assert skill["independent_sample_count"] == 1


def test_hinted_answers_are_not_independent_mastery():
    body = aggregate_report(
        session_id="s",
        mode="STRICT",
        topics=[{"requirement_id": "req_01", "text": "幂等"}],
        assessments=[
            {
                "id": "a1",
                "turn_id": "t1",
                "dimensions": [
                    {
                        "skill_id": "req_01",
                        "level": 3,
                        "evidence_status": EvidenceStatus.DEMONSTRATED,
                        "demonstrated": "照着示范写了测试集。",
                        "missing": "",
                        "hint_level": HintLevel.EXAMPLE,
                    }
                ],
                "verification_gaps": [],
            },
            {
                "id": "a2",
                "turn_id": "t2",
                "dimensions": [
                    {
                        "skill_id": "req_01",
                        "level": 1,
                        "evidence_status": EvidenceStatus.PARTIAL,
                        "demonstrated": "提到了队列。",
                        "missing": "没有对照。",
                        "hint_level": HintLevel.NONE,
                    }
                ],
                "verification_gaps": [],
            },
        ],
        missing_turn_ids=[],
        cutoff_turn_no=2,
    )
    skill = body["coverage"][0]
    assert skill["sample_count"] == 2
    assert skill["independent_sample_count"] == 1
    assert body["next_training"] == ["req_01"]


def test_failed_turns_are_missing_not_zero():
    body = aggregate_report(
        session_id="s",
        mode="STRICT",
        topics=[],
        assessments=[],
        missing_turn_ids=["turn-failed"],
        cutoff_turn_no=1,
    )
    assert str(body["integrity"]) == "PARTIAL"
    assert body["missing_turn_ids"] == ["turn-failed"]
    assert body["coverage"] == []
