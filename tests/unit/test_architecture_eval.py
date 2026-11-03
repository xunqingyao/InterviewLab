import argparse
import csv
import json
from copy import deepcopy

import httpx
import pytest
from evals import architecture
from evals.evaluator_quality import (
    load_cases,
    plan,
    prepare,
    repeat_consistency,
    summarize,
)
from evals.identifiers import identifier_map, replace_ids, restore_ids


def arguments(tmp_path, **changes):
    values = dict(
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "runs",
        mode="fake",
        split="dev",
        limit=2,
        repeats=1,
        total_output_tokens=3600,
        include_shared=False,
        allow_draft=True,
        max_requests=None,
        seed=97,
    )
    values.update(changes)
    return argparse.Namespace(**values)


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_plan_counts_requests_and_only_selects_predeclared_conditions(tmp_path):
    args = arguments(tmp_path, limit=None, split="test", repeats=3)
    prepare(args.data_dir)
    result = architecture.experiment_plan(args)
    assert (result["case_count"], result["root_question_groups"]) == (14, 7)
    assert result["planned_requests"] == 126
    assert result["human_confirmed"] == 0 and not result["api_called"]
    args.include_shared = True
    assert architecture.experiment_plan(args)["planned_requests"] == 210
    with pytest.raises(ValueError, match="unconfirmed"):
        architecture.select_cases(arguments(tmp_path, allow_draft=False))


def test_both_arms_share_facts_gold_is_absent_and_caps_match(tmp_path):
    args = arguments(tmp_path)
    prepare(args.data_dir)
    cases, _ = architecture.select_cases(args)
    case = deepcopy(cases[0])
    case["label"]["reason"] = "GOLD_REASON_SENTINEL"
    case["suggested_reason"] = "SUGGESTION_SENTINEL"
    joint = architecture.request_body(case, "joint", "fixed-model", 3600)
    interview = architecture.request_body(case, "interviewer", "fixed-model", 1800)
    scoring = architecture.request_body(case, "evaluator", "fixed-model", 1800)
    j = json.loads(joint["messages"][1]["content"])
    i = json.loads(interview["messages"][1]["content"])
    e = json.loads(scoring["messages"][1]["content"])
    assert j["context"] == {"interview_context": i["context"], "evaluation_context": e["context"]}
    assert joint["max_tokens"] == interview["max_tokens"] + scoring["max_tokens"]
    assert {x["name"] for x in j["tools"]} == {x["name"] for x in i["tools"] + e["tools"]}
    assert "rubric" not in i["context"] and "claims" not in e["context"]
    assert (
        j["schema"]["$defs"]["AssessmentResult"]["properties"]["turn_id"]["const"]
        == (e["schema"]["properties"]["turn_id"]["const"])
    )
    serialized = json.dumps([joint, interview, scoring])
    assert "GOLD_REASON_SENTINEL" not in serialized and "SUGGESTION_SENTINEL" not in serialized
    assert "suggested_levels" not in serialized and '"label"' not in serialized
    assert case["case_id"] not in serialized  # Even IDs must not leak the suggested grade suffix.
    mapping = identifier_map(case["snapshot"])
    opaque = replace_ids(case["snapshot"], mapping)
    assert opaque["answer_text"] == case["snapshot"]["answer_text"]
    assert opaque["question_text"] == case["snapshot"]["question_text"]
    assert restore_ids(opaque, case["snapshot"]) == case["snapshot"]
    # An identifier mentioned as technical evidence must remain verbatim.
    literal = {"answer_text": case["snapshot"]["turn_id"], "turn_id": case["snapshot"]["turn_id"]}
    hidden = replace_ids(literal, mapping)
    assert hidden["answer_text"] == literal["answer_text"] and hidden["turn_id"] != literal["turn_id"]


def test_shared_context_control_changes_only_the_other_role_context(tmp_path):
    args = arguments(tmp_path)
    prepare(args.data_dir)
    case = architecture.select_cases(args)[0][0]
    for role in ("interviewer", "evaluator"):
        ordinary = architecture.request_body(case, role, "m", 1800)
        shared = architecture.request_body(case, role, "m", 1800, shared=True)
        assert ordinary["messages"][0] == shared["messages"][0]
        ordinary_payload = json.loads(ordinary["messages"][1]["content"])
        shared_payload = json.loads(shared["messages"][1]["content"])
        assert shared_payload["context"].pop("other_role_context")
        assert ordinary_payload == shared_payload


async def test_fake_runs_never_call_live_and_blind_review_preserves_pending_and_ties(tmp_path, monkeypatch):
    args = arguments(tmp_path)
    prepare(args.data_dir)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("fake must never call live")

    monkeypatch.setattr(architecture, "call_live", forbidden)
    result = await architecture.run_experiment(args)
    directory = args.output_dir / result["run_dir"].split("/")[-1]
    summary = architecture.summarize_review(directory)
    assert summary["pending_pairs"] == 2 and summary["reviewed_pairs"] == 0
    assert summary["multi_win_rate_including_ties"]["rate"] is None
    rows = read_csv(directory / "blind_review.csv")
    assert "single_joint" not in json.dumps(rows) and "multi_isolated" not in json.dumps(rows)
    for row in rows:
        row.update(reviewer="actual test reviewer", notes="双方问题相同，暂按同等级评价")
        row.update({f"{b}_{n}": "1" for b in ("A", "B") for n in architecture.RATING_FIELDS})
    write_csv(directory / "blind_review.csv", rows)
    summary = architecture.summarize_review(directory)
    assert summary["paired_counts"] == {"multi_win": 0, "single_win": 0, "tie": 2}
    assert summary["pending_pairs"] == 0
    assert summary["multi_win_rate_including_ties"]["denominator"] == 2
    assert not summary["eligible_for_scoped_live_claim"]
    rows[0]["answer"] = "altered answer"
    write_csv(directory / "blind_review.csv", rows)
    with pytest.raises(ValueError, match="frozen review evidence"):
        architecture.summarize_review(directory)


async def test_live_transport_failure_is_counted_no_retry_or_fake_fallback(tmp_path, monkeypatch):
    args = arguments(tmp_path, mode="live", limit=1, max_requests=3)
    prepare(args.data_dir)
    case = architecture.select_cases(args)[0][0]
    monkeypatch.setattr(architecture.settings, "model_api_key", "test-key")
    called = []

    async def mock_live(client, body, record):
        role = json.loads(body["messages"][1]["content"])["role"]
        called.append(role)
        if role == "evaluator":
            raise httpx.ReadTimeout("simulated")
        record.update(returned_model="mock-provider-version", usage={"total_tokens": 20})
        return await architecture.fake_output(case, role)

    monkeypatch.setattr(architecture, "call_live", mock_live)
    result = await architecture.run_experiment(args)
    directory = args.output_dir / result["run_dir"].split("/")[-1]
    report = json.loads((directory / "report.json").read_text())
    assert sorted(called) == ["evaluator", "interviewer", "joint"]
    assert report["by_arm"]["multi_isolated"]["scoring"]["valid_result_rate"]["rate"] == 0
    assert report["by_arm"]["multi_isolated"]["question_contract_valid"]["rate"] == 1
    assert report["by_arm"]["multi_isolated"]["both_tasks_valid"]["rate"] == 0
    assert report["by_arm"]["multi_isolated"]["requests"] == 2
    args.max_requests = 2
    with pytest.raises(ValueError, match="max-requests >= 3"):
        await architecture.run_experiment(args)
    assert len(called) == 3


async def test_invalid_questions_cannot_receive_positive_human_ratings(tmp_path, monkeypatch):
    args = arguments(tmp_path, limit=1)
    prepare(args.data_dir)
    original = architecture.fake_output

    async def invalid_question(case, role):
        raw = await original(case, role)
        if role == "joint":
            raw["next_question"]["skill_ids"] = ["other-user-requirement"]
        elif role == "interviewer":
            raw["skill_ids"] = ["other-user-requirement"]
        return raw

    monkeypatch.setattr(architecture, "fake_output", invalid_question)
    result = await architecture.run_experiment(args)
    directory = args.output_dir / result["run_dir"].split("/")[-1]
    rows = read_csv(directory / "blind_review.csv")
    row = rows[0]
    assert json.loads(row["A_output"]) == json.loads(row["B_output"]) == {"status": "FAILED"}
    row.update(reviewer="test reviewer", notes="invalid binding")
    row.update({f"{b}_{n}": "1" for b in ("A", "B") for n in architecture.RATING_FIELDS})
    write_csv(directory / "blind_review.csv", rows)
    with pytest.raises(ValueError, match="four zero"):
        architecture.summarize_review(directory)


def test_grading_stability_counts_failures_and_mae_requires_coverage():
    common = {"case_id": "c", "group_id": "g", "label": {"levels": [2]}, "raw_citations": 1}
    rows = [
        {**common, "status": "ok", "level": 2},
        {**common, "status": "TimeoutError"},
        {**common, "case_id": "other", "status": "ok", "level": None},
    ]
    assert repeat_consistency(rows)["all_repeats_same_level"]["rate"] == 0
    metrics = summarize(rows)
    assert metrics["grade_agreement"]["rate"] == 1 / 3
    assert metrics["mae_on_numeric_outputs"] == 0
    assert metrics["numeric_output_coverage"]["rate"] == 1 / 3


def test_grading_plan_does_not_require_labels_but_selected_gold_cannot_be_skipped(tmp_path):
    prepare(tmp_path)
    args = argparse.Namespace(data_dir=tmp_path, split="test", limit=None, repeats=3, max_output_tokens=1800)
    assert plan(args)["planned_requests"] == 108
    with pytest.raises(ValueError, match="requested case IDs"):
        load_cases(tmp_path, "test", True, None, case_ids={"citation_1"})
    labels = read_csv(tmp_path / "labels.csv")
    for row in labels:
        if row["case_id"] == "citation_1":
            row.update(status="CONFIRMED", expected_levels="[1]", reason="测试人工标注", reviewer="test")
    write_csv(tmp_path / "labels.csv", labels)
    selected, _ = load_cases(tmp_path, "dev", False, None, case_ids={"citation_1"})
    assert len(selected) == 1 and selected[0]["label"]["confirmed"]
