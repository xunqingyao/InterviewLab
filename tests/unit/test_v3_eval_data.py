import json

import pytest
from evals.evaluator_quality import input_digest, load_cases
from evals.quality_cases import quality_cases
from evals.v3_cases import VERSION, fresh_cases, write_dataset


def test_fresh_scenarios_have_distinct_questions_and_all_rubric_profiles():
    cases = fresh_cases()
    assert len(cases) == 40 and len({c["group_id"] for c in cases}) == 8
    assert not {c["snapshot"]["question_text"] for c in cases} & {
        c["snapshot"]["question_text"] for c in quality_cases()
    }
    assert {c["snapshot"]["rubric_criteria"]["profile"] for c in cases} == {
        "concept",
        "implementation",
        "tradeoff",
        "verification",
        "troubleshooting",
        "ownership",
    }
    for group in {c["group_id"] for c in cases}:
        assert {tuple(c["suggested_levels"]) for c in cases if c["group_id"] == group} == {
            (0,),
            (1,),
            (2,),
            (3,),
            (None,),
        }


def test_freeze_preserves_ai_provenance_and_material_changes_invalidate_labels(tmp_path):
    cases = fresh_cases()
    write_dataset(tmp_path, cases, VERSION)
    with pytest.raises(ValueError, match="already exists"):
        write_dataset(tmp_path, cases, VERSION)
    with pytest.raises(ValueError, match="unconfirmed"):
        load_cases(tmp_path, "test", False, None)
    selected, _ = load_cases(tmp_path, "test", True, None)
    assert len(selected) == 40 and all(not c["label"]["confirmed"] for c in selected)
    dataset = json.loads((tmp_path / "dataset.json").read_text())
    original_hash = input_digest(dataset["cases"][0])
    dataset["cases"][0]["interview_materials"]["claim_text"] = "替换后的简历经历。"
    assert input_digest(dataset["cases"][0]) != original_hash
    (tmp_path / "dataset.json").write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="input changed"):
        load_cases(tmp_path, "test", True, None)
