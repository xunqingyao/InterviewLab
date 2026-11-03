"""Bounded evaluator quality runs, with explicit human-label provenance and no live fallback.

Prepare: python -m evals.evaluator_quality prepare
Smoke:   python -m evals.evaluator_quality run --mode fake --allow-draft --limit 5
Live:    python -m evals.evaluator_quality run --mode live --max-requests 36 --split test
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import time
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from agents.context import build_evaluator_context
from agents.evaluator_tools import EVALUATOR_TOOL_SPECS, consult_evaluator_tools
from agents.prompts import EVALUATOR_V3
from db.settings import settings
from domain.assessments import sanitize_assessment_result
from domain.schemas import AssessmentResult
from domain.text import locate_quote
from evals.identifiers import identifier_map, replace_ids, restore_ids
from evals.quality_cases import DATASET_VERSION, quality_cases
from llm_gateway.fake import FakeGateway
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext
from llm_gateway.providers import build_provider_request

DEFAULT_DATA = Path(__file__).parent / "quality"
REVIEW_FIELDS = [
    "case_id",
    "input_sha256",
    "split",
    "question",
    "answer",
    "prior_thread",
    "rubric",
    "proposed_levels",
    "proposed_reason",
    "expected_levels",
    "reason",
    "reviewer",
    "status",
]


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def input_digest(case: dict) -> str:
    frozen = {key: case[key] for key in ("case_id", "group_id", "split", "snapshot")}
    if "interview_materials" in case:
        frozen["interview_materials"] = case["interview_materials"]
    return digest(frozen)


def prepare(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    dataset_path, review_path = directory / "dataset.json", directory / "labels.csv"
    if dataset_path.exists() or review_path.exists():
        raise ValueError(
            "dataset.json or labels.csv already exists; preserve manual labels or use a new --data-dir"
        )
    cases = quality_cases()
    dataset_path.write_text(
        json.dumps({"version": DATASET_VERSION, "cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with review_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for case in cases:
            snap = case["snapshot"]
            writer.writerow(
                {
                    "case_id": case["case_id"],
                    "input_sha256": input_digest(case),
                    "split": case["split"],
                    "question": snap["question_text"],
                    "answer": snap["answer_text"],
                    "prior_thread": json.dumps(snap["thread"][:-1], ensure_ascii=False),
                    "rubric": json.dumps(snap["rubric_criteria"], ensure_ascii=False),
                    "proposed_levels": json.dumps(case["suggested_levels"]),
                    "proposed_reason": case["suggested_reason"],
                    "status": "DRAFT",
                }
            )
    return {
        "dataset": str(dataset_path.resolve()),
        "labels": str(review_path.resolve()),
        "cases": len(cases),
        "groups": len({c["group_id"] for c in cases}),
        "splits": dict(Counter(c["split"] for c in cases)),
        "human_confirmed": 0,
    }


def extend_dataset(directory: Path) -> dict:
    """Append new cases, preserving all existing data/labels and backing up both files first."""
    dataset_path, review_path = directory / "dataset.json", directory / "labels.csv"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    with review_path.open(newline="", encoding="utf-8-sig") as handle:
        existing_rows = list(csv.DictReader(handle))
    existing = {case["case_id"]: case for case in dataset["cases"]}
    labels = {row["case_id"]: row for row in existing_rows}
    if len(labels) != len(existing_rows) or set(labels) != set(existing):
        raise ValueError("existing label IDs must match dataset IDs")
    added, repartitioned = [], []
    for case in quality_cases():
        if case["case_id"] in existing:
            previous = existing[case["case_id"]]
            if input_digest(case) != input_digest(previous):
                # A seen test group can move to dev; never silently move development examples to test.
                only_split_changed = {key: value for key, value in case.items() if key != "split"} == {
                    key: value for key, value in previous.items() if key != "split"
                }
                if not only_split_changed or case["split"] != "dev" or previous["split"] != "test":
                    raise ValueError(f"existing input changed; review explicitly: {case['case_id']}")
                previous["split"] = "dev"
                row = labels[case["case_id"]]
                row.update(split="dev", input_sha256=input_digest(previous), status="DRAFT")
                repartitioned.append(case["case_id"])
            continue
        snap = case["snapshot"]
        dataset["cases"].append(case)
        existing_rows.append(
            {
                "case_id": case["case_id"],
                "input_sha256": input_digest(case),
                "split": case["split"],
                "question": snap["question_text"],
                "answer": snap["answer_text"],
                "prior_thread": json.dumps(snap["thread"][:-1], ensure_ascii=False),
                "rubric": json.dumps(snap["rubric_criteria"], ensure_ascii=False),
                "proposed_levels": json.dumps(case["suggested_levels"]),
                "proposed_reason": case["suggested_reason"],
                "status": "DRAFT",
            }
        )
        added.append(case["case_id"])
    if not added and not repartitioned:
        return {"added": [], "repartitioned_to_dev": [], "cases": len(dataset["cases"])}
    backup = directory / "backups" / uuid.uuid4().hex
    backup.mkdir(parents=True)
    (backup / "dataset.json").write_bytes(dataset_path.read_bytes())
    (backup / "labels.csv").write_bytes(review_path.read_bytes())
    dataset["version"] = DATASET_VERSION
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    with review_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(existing_rows)
    return {
        "added": added,
        "repartitioned_to_dev": repartitioned,
        "cases": len(dataset["cases"]),
        "backup": str(backup.resolve()),
    }


def load_cases(
    directory: Path,
    split: str,
    allow_draft: bool,
    limit: int | None,
    *,
    case_ids: set[str] | None = None,
) -> tuple[list[dict], str]:
    dataset = json.loads((directory / "dataset.json").read_text(encoding="utf-8"))
    source = dataset["cases"]
    ids = [case["case_id"] for case in source]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case_id")
    group_splits: dict[str, str] = {}
    for case in source:
        previous = group_splits.setdefault(case["group_id"], case["split"])
        if previous != case["split"]:
            raise ValueError("variants of one group cross dev/test splits")
    with (directory / "labels.csv").open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    labels = {row["case_id"]: row for row in rows}
    if len(labels) != len(rows) or set(labels) != set(ids):
        raise ValueError("label IDs must exactly match dataset IDs, without duplicates")
    selected = []
    requested = [case for case in source if split == "all" or case["split"] == split]
    if case_ids is not None:
        if not case_ids.issubset({case["case_id"] for case in requested}):
            raise ValueError("requested case IDs do not belong to the selected split")
        requested = [case for case in requested if case["case_id"] in case_ids]
    if limit is not None:
        requested = requested[:limit]
    for original in requested:
        case = dict(original)
        row = labels[case["case_id"]]
        if row["input_sha256"] != input_digest(case):
            raise ValueError(f"input changed after label export: {case['case_id']}")
        confirmed = row["status"].strip().upper() == "CONFIRMED"
        if confirmed:
            if not row["reviewer"].strip() or not row["reason"].strip():
                raise ValueError(f"confirmed label lacks reviewer/reason: {case['case_id']}")
            levels = json.loads(row["expected_levels"])
            if (
                not isinstance(levels, list)
                or not levels
                or any(
                    value is not None and (type(value) is not int or value not in range(4))
                    for value in levels
                )
                or (None in levels and levels != [None])
            ):
                raise ValueError(f"expected_levels must be [null] or numeric levels 0..3: {case['case_id']}")
        elif allow_draft:
            levels = case["suggested_levels"]
        else:
            # Fail closed instead of silently reporting only the easy, already-labelled subset.
            raise ValueError(
                f"unconfirmed label: {case['case_id']}; "
                "review labels.csv or explicitly use --allow-draft for exploration"
            )
        case["label"] = {
            "levels": levels,
            "confirmed": confirmed,
            "reviewer": row["reviewer"].strip() if confirmed else "",
            "reason": row["reason"].strip() if confirmed else case["suggested_reason"],
        }
        selected.append(case)
    if not selected:
        raise ValueError("no selected cases")
    return selected, dataset["version"]


def model_context(snapshot: dict) -> dict:
    pack = build_evaluator_context(snapshot)
    pack["tool_results"] = consult_evaluator_tools(snapshot, owner_id="offline-quality")
    return pack


def provider_context(snapshot: dict) -> dict:
    return replace_ids(model_context(snapshot), identifier_map(snapshot))


def citation_stats(raw: Any, snapshot: dict) -> dict:
    sources = {
        row["message_id"]: row["text"] for row in build_evaluator_context(snapshot)["evidence_answers"]
    }
    count = valid = 0
    if isinstance(raw, dict) and isinstance(raw.get("dimensions"), list):
        for dimension in raw["dimensions"]:
            if not isinstance(dimension, dict) or not isinstance(dimension.get("answer_evidence"), list):
                continue
            for span in dimension["answer_evidence"]:
                count += 1
                if not isinstance(span, dict):
                    continue
                message_id, quote = span.get("message_id"), span.get("quote")
                if isinstance(message_id, str) and isinstance(quote, str) and message_id in sources:
                    valid += locate_quote(sources[message_id], quote) is not None
    return {"raw_citations": count, "valid_raw_citations": valid}


def check_result(raw: Any, snapshot: dict) -> dict:
    parsed = AssessmentResult.model_validate(raw)
    if len(parsed.dimensions) != 1 or parsed.dimensions[0].skill_id != "req_01":
        raise ValueError("expected exactly one assessment dimension for req_01")
    pack = build_evaluator_context(snapshot)
    sources = {row["message_id"]: row["text"] for row in pack["evidence_answers"]}
    cleaned = sanitize_assessment_result(
        parsed,
        answer_message_id=snapshot["answer_message_id"],
        answer_text=snapshot["answer_text"],
        answer_sources=sources,
        expected_turn_id=snapshot["turn_id"],
        expected_rubric_id=snapshot["rubric_snapshot_id"],
        allowed_skill_ids=snapshot["skill_ids"],
        allowed_reference_ids=[row["id"] for row in snapshot.get("reference_hits", [])],
        expected_hint_level=snapshot["hint_level"],
    )
    dimension = cleaned.dimensions[0]
    return {
        "cleaned": cleaned.model_dump(mode="json"),
        "level": dimension.level,
        "evidence_status": dimension.evidence_status.value,
    }


def fraction(numerator: int, denominator: int) -> dict:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
    }


def summarize(results: list[dict]) -> dict:
    numeric = [r for r in results if None not in r["label"]["levels"]]
    nulls = [r for r in results if r["label"]["levels"] == [None]]
    errors = [r for r in numeric if r["label"]["levels"] == [0]]
    success = [r for r in results if r["status"] == "ok"]
    statuses = {"INSUFFICIENT_EVIDENCE"}
    numeric_outputs = [r for r in numeric if r["status"] == "ok" and r["level"] is not None]
    return {
        "attempt_count": len(results),
        "unique_cases": len({r["case_id"] for r in results}),
        "unique_groups": len({r["group_id"] for r in results}),
        "valid_result_rate": fraction(len(success), len(results)),
        "grade_agreement": fraction(
            sum(r["status"] == "ok" and r["level"] in r["label"]["levels"] for r in numeric), len(numeric)
        ),
        "numeric_output_coverage": fraction(len(numeric_outputs), len(numeric)),
        "mae_on_numeric_outputs": (
            sum(min(abs(r["level"] - gold) for gold in r["label"]["levels"]) for r in numeric_outputs)
            / len(numeric_outputs)
            if numeric_outputs
            else None
        ),
        "wrong_answer_high_score_rate": fraction(
            sum(r["status"] == "ok" and r["level"] in (2, 3) for r in errors), len(errors)
        ),
        "wrong_answer_abstentions": sum(r["status"] == "ok" and r["level"] is None for r in errors),
        "wrong_answer_failures": sum(r["status"] != "ok" for r in errors),
        "correct_abstention_rate": fraction(
            sum(
                r["status"] == "ok" and r["level"] is None and r["evidence_status"] in statuses for r in nulls
            ),
            len(nulls),
        ),
        "raw_citation_validity": fraction(
            sum(r.get("valid_raw_citations", 0) for r in results),
            sum(r.get("raw_citations", 0) for r in results),
        ),
        "numeric_results_without_raw_citations": sum(
            r["status"] == "ok" and r["raw_citations"] == 0 for r in numeric
        ),
        "failure_types": dict(Counter(r["status"] for r in results if r["status"] != "ok")),
        "evidence_support_review": "PENDING_HUMAN_REVIEW; citation validity does not measure entailment",
    }


def repeat_consistency(results: list[dict]) -> dict:
    """Failures are unstable; repeated draws never increase the independent case count."""
    groups = defaultdict(list)
    for row in results:
        groups[row["case_id"]].append(row)
    repeated = [rows for rows in groups.values() if len(rows) > 1]
    return {
        "all_repeats_same_level": fraction(
            sum(
                all(r["status"] == "ok" for r in rows) and len({r["level"] for r in rows}) == 1
                for rows in repeated
            ),
            len(repeated),
        ),
        "note": "Includes null as a level; failures are unstable. Stability does not imply accuracy.",
    }


def plan(args: argparse.Namespace) -> dict:
    cases, version = load_cases(args.data_dir, args.split, True, args.limit)
    return {
        "dataset_version": version,
        "split": args.split,
        "cases": len(cases),
        "root_question_groups": len({c["group_id"] for c in cases}),
        "rubric_profiles": dict(Counter(c["snapshot"]["rubric_criteria"]["profile"] for c in cases)),
        "human_confirmed": sum(c["label"]["confirmed"] for c in cases),
        "repeats": args.repeats,
        "planned_requests": len(cases) * args.repeats,
        "max_output_tokens_per_request": args.max_output_tokens,
        "api_called": False,
    }


async def run(args: argparse.Namespace) -> dict:
    cases, version = load_cases(args.data_dir, args.split, args.allow_draft, args.limit)
    planned = len(cases) * args.repeats
    if args.mode == "live":
        if not settings.model_api_key:
            raise ValueError("live evaluation requires an API key; FakeGateway fallback is disabled")
        if args.max_requests is None or args.max_requests < planned:
            raise ValueError(
                f"this selection requires {planned} calls; set --max-requests at least {planned}, "
                "or reduce --limit/--repeats"
            )
    prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else EVALUATOR_V3
    if not prompt.strip():
        raise ValueError("empty prompt")
    now = datetime.now(UTC)
    run_id = f"{now.strftime('%Y%m%dT%H%M%SZ')}_{args.mode}_{uuid.uuid4().hex[:8]}"
    out = args.output_dir / run_id
    out.mkdir(parents=True)
    model = settings.model_identifier if args.mode == "live" else "fake-v1"
    endpoint_hash = (
        digest(settings.model_api_base or "https://api.openai.com/v1") if args.mode == "live" else None
    )
    root = Path(__file__).resolve().parents[1]
    pipeline_hash = digest(
        {
            name: hashlib.sha256((root / "packages" / name).read_bytes()).hexdigest()
            for name in (
                "agents/context.py",
                "agents/evaluator_tools.py",
                "domain/schemas.py",
                "domain/assessments.py",
                "domain/text.py",
                "llm_gateway/providers.py",
            )
        }
    )
    pipeline_hash = digest(
        {
            "production": pipeline_hash,
            "eval_identifiers": hashlib.sha256((root / "evals/identifiers.py").read_bytes()).hexdigest(),
            "eval_runner": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
    )
    metadata = {
        "run_id": run_id,
        "created_at_utc": now.isoformat(),
        "mode": args.mode,
        "model": model,
        "provider_endpoint_sha256": endpoint_hash,
        "dataset_version": version,
        "dataset_sha256": digest(cases),
        "prompt_sha256": digest(prompt),
        "prompt_text": prompt,
        "split": args.split,
        "case_count": len(cases),
        "planned_requests": planned,
        "repeats": args.repeats,
        "limited_selection": args.limit is not None,
        "temperature": 0.2,
        "max_output_tokens": args.max_output_tokens,
        "pipeline_sha256": pipeline_hash,
        "all_labels_human_confirmed": all(c["label"]["confirmed"] for c in cases),
        "reviewers": sorted({c["label"]["reviewer"] for c in cases if c["label"]["confirmed"]}),
        "quality_claim_allowed": args.mode == "live" and all(c["label"]["confirmed"] for c in cases),
        "quality_claim_scope": (
            "Synthetic labelled cases only; evidence support still needs human review. "
            "No architecture superiority or improvement claim from a single run."
        ),
        "comparison_fingerprint": digest(
            {
                "cases": cases,
                "model": model,
                "provider_endpoint_sha256": endpoint_hash,
                "repeats": args.repeats,
                "temperature": 0.2,
                "max_output_tokens": args.max_output_tokens,
                "pipeline_sha256": pipeline_hash,
                "contexts": [provider_context(case["snapshot"]) for case in cases],
            }
        ),
    }
    # Persist full inputs separately from model-facing contexts. Gold labels never enter the request.
    (out / "inputs.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    fake = FakeGateway()
    async with httpx.AsyncClient(timeout=45, transport=httpx.AsyncHTTPTransport(retries=0)) as client:
        with (out / "results.jsonl").open("w", encoding="utf-8") as handle:
            for repeat in range(args.repeats):
                for case in cases:
                    snapshot = case["snapshot"]
                    context = provider_context(snapshot)
                    input_snapshot = AgentInputSnapshot(
                        id=snapshot["id"], role=AgentRole.EVALUATOR, payload=context
                    )
                    result = {
                        "case_id": case["case_id"],
                        "group_id": case["group_id"],
                        "split": case["split"],
                        "repeat": repeat + 1,
                        "label": case["label"],
                        "status": "pending",
                    }
                    started = time.monotonic()
                    try:
                        body, _ = build_provider_request(
                            role=AgentRole.EVALUATOR,
                            input_snapshot=input_snapshot,
                            output_schema=AssessmentResult,
                            tools=EVALUATOR_TOOL_SPECS,
                            model=model,
                            prompt_override=prompt,
                            max_output_tokens=args.max_output_tokens,
                        )
                        result["request_body"] = body
                        if args.mode == "fake":
                            response = await fake.generate_structured(
                                role=AgentRole.EVALUATOR,
                                input_snapshot=input_snapshot,
                                output_schema=AssessmentResult,
                                tools=EVALUATOR_TOOL_SPECS,
                                budget=BudgetContext(owner_id="offline-quality"),
                            )
                            result["raw_output"] = response.data
                        else:
                            base = settings.model_api_base or "https://api.openai.com/v1"
                            response = await client.post(
                                f"{base.rstrip('/')}/chat/completions",
                                headers={"Authorization": f"Bearer {settings.model_api_key}"},
                                json=body,
                            )
                            result["http_status"] = response.status_code
                            response.raise_for_status()
                            payload = response.json()
                            result["returned_model"] = payload.get("model")
                            result["provider_request_id"] = response.headers.get(
                                "x-request-id"
                            ) or payload.get("id")
                            result["usage"] = payload.get("usage")
                            result["raw_content"] = payload["choices"][0]["message"]["content"]
                            result["raw_output"] = json.loads(result["raw_content"])
                        provider_snapshot = replace_ids(snapshot, identifier_map(snapshot))
                        result.update(citation_stats(result["raw_output"], provider_snapshot))
                        result.update(check_result(result["raw_output"], provider_snapshot))
                        result["cleaned"] = restore_ids(result["cleaned"], snapshot)
                        result["status"] = "ok"
                    except Exception as exc:
                        # Omit exception strings, headers, credentials and provider error bodies.
                        result["status"] = type(exc).__name__
                    result["latency_seconds"] = time.monotonic() - started
                    results.append(result)
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                    handle.flush()
                    print(
                        json.dumps(
                            {"case_id": case["case_id"], "repeat": repeat + 1, "status": result["status"]}
                        ),
                        flush=True,
                    )
    report = {
        "metadata": metadata,
        "metrics": summarize(results),
        "repeat_consistency": repeat_consistency(results),
        "by_group": {
            group: summarize([r for r in results if r["group_id"] == group])
            for group in sorted({r["group_id"] for r in results})
        },
        "by_profile": {
            profile: summarize(
                [
                    r
                    for r in results
                    if r["case_id"]
                    in {c["case_id"] for c in cases if c["snapshot"]["rubric_criteria"]["profile"] == profile}
                ]
            )
            for profile in sorted({c["snapshot"]["rubric_criteria"]["profile"] for c in cases})
        },
        "usage": {
            key: sum((r.get("usage") or {}).get(key, 0) or 0 for r in results)
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "latency_seconds_mean": sum(r["latency_seconds"] for r in results) / len(results),
        "requests_with_usage": sum(bool(r.get("usage")) for r in results),
        "actual_cost": None,
        "notes": (
            "Fake or draft-label runs are exploratory only. No actual cost inferred from budget estimates. "
            "Repeats are not independent cases."
        ),
    }
    observations = defaultdict(list)
    for row in results:
        observations[row["case_id"]].append(row.get("level") if row["status"] == "ok" else row["status"])
    report["level_distributions"] = dict(observations)
    metadata["returned_models"] = sorted({r["returned_model"] for r in results if r.get("returned_model")})
    metadata["results_sha256"] = digest(results)
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "evidence_review.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        fields = [
            "case_id",
            "repeat",
            "question",
            "answer",
            "prior_thread",
            "rubric",
            "cleaned_output",
            "supports_conclusion",
            "reviewer",
            "notes",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        by_id = {case["case_id"]: case for case in cases}
        for row in results:
            snap = by_id[row["case_id"]]["snapshot"]
            writer.writerow(
                {
                    "case_id": row["case_id"],
                    "repeat": row["repeat"],
                    "question": snap["question_text"],
                    "answer": snap["answer_text"],
                    "prior_thread": json.dumps(snap["thread"][:-1], ensure_ascii=False),
                    "rubric": json.dumps(snap["rubric_criteria"], ensure_ascii=False),
                    "cleaned_output": json.dumps(row.get("cleaned"), ensure_ascii=False),
                }
            )
    return {
        "report": str((out / "report.json").resolve()),
        "mode": args.mode,
        "human_confirmed": metadata["all_labels_human_confirmed"],
        "requests": planned,
        "resume_use": "Review scope and evidence before citing live numbers"
        if metadata["quality_claim_allowed"]
        else "NOT_ELIGIBLE: fake model or unconfirmed labels",
    }


def compare(before: Path, after: Path) -> dict:
    old = json.loads(before.read_text(encoding="utf-8"))
    new = json.loads(after.read_text(encoding="utf-8"))
    if old["metadata"]["comparison_fingerprint"] != new["metadata"]["comparison_fingerprint"]:
        raise ValueError(
            "runs differ in inputs, labels, model, pipeline or parameters; not a prompt-only comparison"
        )
    if old["metadata"].get("returned_models") != new["metadata"].get("returned_models"):
        raise ValueError("provider returned different model identifiers; not a prompt-only comparison")
    names = (
        "valid_result_rate",
        "grade_agreement",
        "wrong_answer_high_score_rate",
        "correct_abstention_rate",
        "raw_citation_validity",
    )
    rates = {}
    for name in names:
        left, right = old["metrics"][name], new["metrics"][name]
        rates[name] = {
            "before": left,
            "after": right,
            "delta": right["rate"] - left["rate"]
            if right["rate"] is not None and left["rate"] is not None
            else None,
        }
    return {
        "mode": old["metadata"]["mode"],
        "same_prompt": old["metadata"]["prompt_sha256"] == new["metadata"]["prompt_sha256"],
        "all_labels_human_confirmed": old["metadata"]["all_labels_human_confirmed"],
        "rates": rates,
        "note": (
            "Only a descriptive comparison. Inspect raw failures and human evidence reviews; "
            "no statistical or architecture superiority claim."
        ),
    }


def summarize_evidence_review(directory: Path) -> dict:
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    inputs = json.loads((directory / "inputs.json").read_text(encoding="utf-8"))
    results = [
        json.loads(line) for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    metadata = report["metadata"]
    if digest(inputs) != metadata["dataset_sha256"] or digest(results) != metadata.get("results_sha256"):
        raise ValueError("run inputs or results changed; review must refer to the frozen run")
    cases = {case["case_id"]: case for case in inputs}
    outputs = {(row["case_id"], row["repeat"]): row for row in results}
    with (directory / "evidence_review.csv").open(newline="", encoding="utf-8-sig") as handle:
        reviews = list(csv.DictReader(handle))
    keys = [(row["case_id"], int(row["repeat"])) for row in reviews]
    if len(set(keys)) != len(keys) or set(keys) != set(outputs):
        raise ValueError("review rows must match every run output exactly once")
    reviewed = supported = eligible = 0
    reviewers = set()
    for key, review in zip(keys, reviews, strict=True):
        output, snap = outputs[key], cases[key[0]]["snapshot"]
        if (
            review["question"] != snap["question_text"]
            or review["answer"] != snap["answer_text"]
            or json.loads(review["prior_thread"]) != snap["thread"][:-1]
            or json.loads(review["rubric"]) != snap["rubric_criteria"]
            or json.loads(review["cleaned_output"]) != output.get("cleaned")
        ):
            raise ValueError("review worksheet changed frozen evidence; edit judgement columns only")
        if output["status"] != "ok":
            continue  # Runtime failures remain failures, not human judgement successes.
        eligible += 1
        judgement = review["supports_conclusion"].strip().upper()
        if not judgement:
            continue
        if judgement not in {"YES", "NO"} or not review["reviewer"].strip() or not review["notes"].strip():
            raise ValueError("each judgement needs YES/NO, the actual reviewer and supporting notes")
        reviewed += 1
        supported += judgement == "YES"
        reviewers.add(review["reviewer"].strip())
    summary = {
        "run_id": metadata["run_id"],
        "reviewed_outputs": reviewed,
        "eligible_outputs": eligible,
        "pending_outputs": eligible - reviewed,
        "output_explanations_supported": fraction(supported, reviewed),
        "reviewers": sorted(reviewers),
        "review_complete": eligible > 0 and reviewed == eligible,
        "mode": metadata["mode"],
        "all_labels_human_confirmed": metadata["all_labels_human_confirmed"],
        "scope": "Human judgement of complete output explanations, not individual-claim accuracy.",
        "worksheet_sha256": hashlib.sha256((directory / "evidence_review.csv").read_bytes()).hexdigest(),
    }
    (directory / "evidence_review_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prep = subparsers.add_parser(
        "prepare", help="export synthetic cases and blank human-confirmation fields; no API calls"
    )
    prep.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    extension = subparsers.add_parser(
        "extend", help="append new diagnostics without overwriting prior labels"
    )
    extension.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    preview = subparsers.add_parser("plan", help="show selection and call count without API requests")
    preview.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    preview.add_argument("--split", choices=("dev", "test", "all"), default="test")
    preview.add_argument("--limit", type=int)
    preview.add_argument("--repeats", type=int, default=3)
    preview.add_argument("--max-output-tokens", type=int, default=1800)
    execute = subparsers.add_parser(
        "run", help="run confirmed samples; live mode requires an explicit request cap"
    )
    execute.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
    execute.add_argument("--output-dir", type=Path, default=DEFAULT_DATA / "runs")
    execute.add_argument("--mode", choices=("fake", "live"), default="fake")
    execute.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    execute.add_argument(
        "--allow-draft", action="store_true", help="exploratory run only; never human gold metrics"
    )
    execute.add_argument("--limit", type=int)
    execute.add_argument("--repeats", type=int, default=1)
    execute.add_argument("--max-requests", type=int)
    execute.add_argument("--max-output-tokens", type=int, default=1800)
    execute.add_argument(
        "--prompt-file",
        type=Path,
        help="candidate evaluator system prompt; default is the current production prompt",
    )
    comparison = subparsers.add_parser(
        "compare", help="compare two reports only when non-prompt conditions match"
    )
    comparison.add_argument("--before", type=Path, required=True)
    comparison.add_argument("--after", type=Path, required=True)
    evidence = subparsers.add_parser("review", help="summarize actual human evidence judgements from one run")
    evidence.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command in {"run", "plan"} and (
        args.repeats < 1 or args.max_output_tokens < 1 or (args.limit is not None and args.limit < 1)
    ):
        parser.error("repeats, limit and max-output-tokens must be positive")
    try:
        if args.command == "prepare":
            result = prepare(args.data_dir)
        elif args.command == "extend":
            result = extend_dataset(args.data_dir)
        elif args.command == "compare":
            result = compare(args.before, args.after)
        elif args.command == "review":
            result = summarize_evidence_review(args.run_dir)
        elif args.command == "plan":
            result = plan(args)
        else:
            result = asyncio.run(run(args))
    except (ValueError, FileNotFoundError, KeyError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
