import argparse
import csv
import json
from pathlib import Path

import pytest
from evals.evaluator_quality import (
    check_result,
    citation_stats,
    compare,
    extend_dataset,
    load_cases,
    model_context,
    prepare,
    run,
    summarize,
    summarize_evidence_review,
)
from evals.quality_cases import quality_cases

from domain.schemas import AssessmentResult
from llm_gateway.interface import AgentInputSnapshot, AgentRole
from llm_gateway.providers import build_provider_request


def response(snapshot, quote=None, message_id=None, level=2):
    return {
        "turn_id": snapshot["turn_id"],
        "rubric_snapshot_id": snapshot["rubric_snapshot_id"],
        "status": "ASSESSED",
        "dimensions": [
            {
                "skill_id": "req_01",
                "level": level,
                "evidence_status": "DEMONSTRATED",
                "answer_evidence": [
                    {
                        "message_id": message_id or snapshot["answer_message_id"],
                        "quote": quote or snapshot["answer_text"],
                    }
                ],
            }
        ],
    }


def test_unique_inputs_keep_question_variants_in_one_split():
    cases = quality_cases()
    assert len(cases) == 70
    assert len({case["group_id"] for case in cases}) == 12
    assert len({case["case_id"] for case in cases}) == len(cases)
    for group in {case["group_id"] for case in cases}:
        assert len({case["split"] for case in cases if case["group_id"] == group}) == 1
    assert {case["snapshot"]["rubric_criteria"]["profile"] for case in cases} == {
        "concept",
        "implementation",
        "tradeoff",
        "verification",
        "ownership",
        "troubleshooting",
    }
    assert all(case["split"] == "dev" for case in cases if case["group_id"] == "injection")


def test_gold_labels_never_enter_model_context():
    case = quality_cases()[0]
    context = model_context(case["snapshot"])
    assert "suggested_levels" not in context
    assert "suggested_reason" not in context
    assert "label" not in context
    assert case["suggested_reason"] not in json.dumps(context, ensure_ascii=False)


def test_prepare_preserves_existing_human_work(tmp_path):
    prepare(tmp_path)
    with pytest.raises(ValueError, match="already exists"):
        prepare(tmp_path)
    with pytest.raises(ValueError, match="unconfirmed"):
        load_cases(tmp_path, "test", False, None)
    cases, _ = load_cases(tmp_path, "test", True, None)
    assert cases and all(not c["label"]["confirmed"] for c in cases)


def test_confirmed_label_requires_reviewer_and_input_hash(tmp_path):
    prepare(tmp_path)
    path = tmp_path / "labels.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    rows[0].update(status="CONFIRMED", expected_levels="[0]", reason="核心概念明确错误", reviewer="")

    def save():
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    save()
    with pytest.raises(ValueError, match="reviewer"):
        load_cases(tmp_path, "dev", False, 1)
    rows[0]["reviewer"] = "test reviewer"
    save()
    selected, _ = load_cases(tmp_path, "dev", False, 1)
    assert selected[0]["label"]["confirmed"]
    rows[0]["input_sha256"] = "changed"
    save()
    with pytest.raises(ValueError, match="input changed"):
        load_cases(tmp_path, "dev", False, 1)


def test_citation_failure_cannot_keep_numeric_score():
    snap = quality_cases()[2]["snapshot"]
    raw = response(snap, quote="根本没说过的证据")
    assert citation_stats(raw, snap) == {"raw_citations": 1, "valid_raw_citations": 0}
    result = check_result(raw, snap)
    assert result["level"] is None
    assert result["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert raw["dimensions"][0]["level"] == 2  # Raw output remains auditable.


def test_answer_source_and_rubric_binding_are_enforced():
    snap = quality_cases()[2]["snapshot"]
    raw = response(snap, message_id="future-or-other-topic")
    assert check_result(raw, snap)["level"] is None
    raw["rubric_snapshot_id"] = "other-rubric"
    with pytest.raises(Exception, match="rubric_snapshot_id"):
        check_result(raw, snap)
    raw = response(snap)
    raw["dimensions"] = []
    with pytest.raises(ValueError, match="exactly one"):
        check_result(raw, snap)


def test_followup_context_contains_prior_evidence():
    case = next(c for c in quality_cases() if c["case_id"] == "thread_repaired")
    pack = model_context(case["snapshot"])
    assert len(pack["evidence_answers"]) == 2
    assert pack["prior_thread"][0]["followup_depth"] == 0
    assert "引用校验会检查这句话是不是出现在用户回答里。" in pack["prior_thread"][0]["answer"]
    assert pack["rubric"]["scope"]["question"] == pack["prior_thread"][0]["question"]


def test_failures_and_abstentions_do_not_inflate_quality_metrics():
    common = {
        "case_id": "c",
        "group_id": "g",
        "label": {"levels": [0]},
        "raw_citations": 1,
        "valid_raw_citations": 0,
    }
    metrics = summarize(
        [
            {**common, "status": "ok", "level": 0, "evidence_status": "MISSING"},
            {**common, "status": "ValueError"},
            {**common, "status": "ok", "level": None, "evidence_status": "INSUFFICIENT_EVIDENCE"},
            {**common, "label": {"levels": [None]}, "status": "TimeoutException"},
        ]
    )
    assert metrics["grade_agreement"]["denominator"] == 3
    assert metrics["grade_agreement"]["numerator"] == 1
    assert metrics["correct_abstention_rate"]["rate"] == 0
    assert metrics["wrong_answer_abstentions"] == 1
    assert metrics["wrong_answer_failures"] == 1
    assert metrics["raw_citation_validity"]["denominator"] == 4


def test_shared_request_keeps_production_defaults_and_allows_eval_prompt():
    case = quality_cases()[0]
    snapshot = AgentInputSnapshot(
        id="test", role=AgentRole.EVALUATOR, payload=model_context(case["snapshot"])
    )
    kwargs = dict(
        role=AgentRole.EVALUATOR,
        input_snapshot=snapshot,
        output_schema=AssessmentResult,
        tools=[],
        model="test-model",
    )
    body, version = build_provider_request(**kwargs)
    assert version == "evaluator_v3"
    assert body["temperature"] == 0.2
    assert "max_tokens" not in body
    candidate, _ = build_provider_request(**kwargs, prompt_override="candidate", max_output_tokens=100)
    assert candidate["messages"][0]["content"] == "candidate"
    assert candidate["messages"][1] == body["messages"][1]
    schema = json.loads(candidate["messages"][1]["content"])["schema"]
    assert schema["properties"]["turn_id"]["const"] == case["snapshot"]["turn_id"]


def test_compare_rejects_changed_non_prompt_conditions(tmp_path):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps({"metadata": {"comparison_fingerprint": "a"}}))
    after.write_text(json.dumps({"metadata": {"comparison_fingerprint": "b"}}))
    with pytest.raises(ValueError, match="not a prompt-only comparison"):
        compare(before, after)


def test_extension_preserves_existing_label_work(tmp_path):
    prepare(tmp_path)
    dataset_path, labels_path = tmp_path / "dataset.json", tmp_path / "labels.csv"
    dataset = json.loads(dataset_path.read_text())
    removed_id = dataset["cases"].pop()["case_id"]
    dataset_path.write_text(json.dumps(dataset))
    with labels_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        rows = [row for row in reader if row["case_id"] != removed_id]
    rows[0]["reason"] = "unfinished human notes must be preserved"
    with labels_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    result = extend_dataset(tmp_path)
    assert result["added"] == [removed_id]
    assert "unfinished human notes must be preserved" in labels_path.read_text(encoding="utf-8-sig")
    assert (tmp_path / "backups").is_dir()


@pytest.mark.asyncio
async def test_live_preflight_never_silently_falls_back_or_exceeds_cap(tmp_path, monkeypatch):
    from db.settings import settings

    prepare(tmp_path)
    args = argparse.Namespace(
        data_dir=tmp_path,
        split="dev",
        allow_draft=True,
        limit=1,
        repeats=1,
        mode="live",
        max_requests=0,
    )
    monkeypatch.setattr(settings, "model_api_key", "")
    with pytest.raises(ValueError, match="fallback is disabled"):
        await run(args)
    monkeypatch.setattr(settings, "model_api_key", "test-not-a-real-key")
    with pytest.raises(ValueError, match="requires 1 calls"):
        await run(args)


@pytest.mark.asyncio
async def test_evidence_review_counts_only_real_completed_judgements(tmp_path):
    prepare(tmp_path)
    args = argparse.Namespace(
        data_dir=tmp_path,
        output_dir=tmp_path / "runs",
        split="dev",
        allow_draft=True,
        limit=1,
        repeats=1,
        mode="fake",
        max_requests=None,
        max_output_tokens=1000,
        prompt_file=None,
    )
    result = await run(args)
    directory = Path(result["report"]).parent
    pending = summarize_evidence_review(directory)
    assert pending["pending_outputs"] == 1 and not pending["review_complete"]
    worksheet = directory / "evidence_review.csv"
    with worksheet.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    rows[0].update(supports_conclusion="NO", reviewer="unit test reviewer", notes="test judgement")

    def save():
        with worksheet.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    save()
    completed = summarize_evidence_review(directory)
    assert completed["review_complete"]
    assert completed["output_explanations_supported"]["rate"] == 0
    assert not completed["all_labels_human_confirmed"]
    rows[0]["answer"] = "rewritten evidence"
    save()
    with pytest.raises(ValueError, match="worksheet changed"):
        summarize_evidence_review(directory)


def test_compare_rejects_changed_returned_model(tmp_path):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps({"metadata": {"comparison_fingerprint": "a", "returned_models": ["v1"]}}))
    after.write_text(json.dumps({"metadata": {"comparison_fingerprint": "a", "returned_models": ["v2"]}}))
    with pytest.raises(ValueError, match="different model"):
        compare(before, after)
