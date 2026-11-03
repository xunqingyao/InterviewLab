"""Paired fixed-turn single-call versus isolated-role benchmark; no database needed.

plan never calls an API. fake only exercises the runner, never measures Agent quality.
Live runs are bounded, preserve requests/outputs, and require human gold or --allow-draft.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import random
import time
import uuid
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import ConfigDict, create_model

from agents.context import build_interviewer_context
from agents.evaluator_tools import EVALUATOR_TOOL_SPECS
from agents.prompts import EVALUATOR_V3, INTERVIEWER_V3
from agents.tools import INTERVIEWER_TOOL_SPECS, consult_interviewer_tools
from db.settings import settings
from domain.questions import materialize_question_decision
from domain.schemas import AssessmentResult, QuestionDecision
from evals.agent_suite import call_live
from evals.evaluator_quality import (
    DEFAULT_DATA,
    check_result,
    citation_stats,
    digest,
    fraction,
    load_cases,
    model_context,
    repeat_consistency,
    summarize,
)
from evals.identifiers import identifier_map, replace_ids, restore_ids
from llm_gateway.fake import FakeGateway
from llm_gateway.interface import AgentInputSnapshot, BudgetContext
from llm_gateway.providers import build_provider_request

VERSION = "fixed_turn_architecture_v2_server_binding"
ARMS = ("single_joint", "multi_isolated")
JointResult = create_model(
    "JointResult",
    __config__=ConfigDict(extra="forbid"),
    assessment=(AssessmentResult, ...),
    next_question=(QuestionDecision, ...),
)
JOINT_PROMPT = (
    """你是同一个模拟面试助手，需要认真完成两项任务：评估已提交回答，决定下一问。
两项任务使用同一份事实，输出一个 JSON，顶层只有 assessment 与 next_question。
先独立完成 assessment，再完成 next_question；评分不应被拟生成的问题反向影响。
interview_context 是出题输入，evaluation_context 是当前回答的评分输入。
两组只读工具结果均已由代码查询并放在对应输入中；不能自行写库或虚构工具结果。
以下评分规则仅适用于 assessment 子对象，出题规则仅适用于 next_question 子对象；
其中“顶层字段”“只返回某 schema”均指对应子对象。最终顶层遵守 JointResult。
【评分规则】
"""
    + EVALUATOR_V3
    + "\n【出题规则】\n"
    + INTERVIEWER_V3
)


def select_cases(args: argparse.Namespace, *, planning: bool = False) -> tuple[list[dict], str]:
    dataset = json.loads((args.data_dir / "dataset.json").read_text(encoding="utf-8"))
    # Two predeclared answer conditions per root, selected by ID, never by model success.
    ids = {
        c["case_id"]
        for c in dataset["cases"]
        if (args.split == "all" or c["split"] == args.split)
        and c["case_id"] in {f"{c['group_id']}_1", f"{c['group_id']}_3"}
    }
    return load_cases(
        args.data_dir,
        args.split,
        planning or args.allow_draft,
        args.limit,
        case_ids=ids,
    )


def interview_snapshot(case: dict) -> dict:
    """Synthetic resume/JD, fixed before treatment; no gold or reference answer enters it."""
    snap = case["snapshot"]
    claim = f"参与模拟面试系统的{snap['requirement_text']}模块设计与实现。"
    if case["group_id"] == "ownership":
        claim = "基于 LangGraph 开发面试系统"
    materials = case.get("interview_materials") or {}
    claim = materials.get("claim_text") or claim
    other = "为模拟面试系统实现了只读工具查询与参数校验。"
    other = materials.get("other_claim_text") or other
    other_requirement = materials.get("other_requirement_text") or "工具使用与权限边界"
    return {
        "id": case["case_id"],
        "resume_plain": claim + "\n" + other,
        "jd_plain": snap["requirement_text"] + "；" + other_requirement,
        "claims": {
            "items": [
                {
                    "id": "claim_01",
                    "text": claim,
                    "project": "模拟面试系统",
                    "kind": "duty",
                    "probe_hints": ["当前问题的核心解释", "具体实现过程", "验证方法与失败边界"],
                },
                {
                    "id": "claim_02",
                    "text": other,
                    "project": "模拟面试系统",
                    "kind": "duty",
                    "probe_hints": materials.get("other_probe_hints")
                    or ["工具授权的实现过程", "工具授权的验证方法"],
                },
            ]
        },
        "requirements": {
            "items": [
                {"id": "req_01", "text": snap["requirement_text"], "priority": "must"},
                {"id": "req_02", "text": other_requirement, "priority": "nice"},
            ]
        },
        "last_turn_id": snap["turn_id"],
        "last_claim_ids": ["claim_01"],
        "last_probe_key": "claim_01:0",
        "last_skill_ids": ["req_01"],
        "last_question": snap["question_text"],
        "last_answer": snap["answer_text"],
        "thread": deepcopy(snap["thread"]),
        "followup_depth": 0,
        "used_claim_ids": ["claim_01"],
        "used_probe_keys": ["claim_01:0"],
        "used_requirement_ids": ["req_01"],
        "used_question_texts": [snap["question_text"]],
        "answered_count": 1,
        "remaining_question_budget": 5,
        "max_questions": 6,
    }


def contexts(case: dict) -> tuple[dict, dict]:
    snap = interview_snapshot(case)
    interviewer = build_interviewer_context(snap)
    interviewer["tool_results"] = consult_interviewer_tools(snap, owner_id="offline-architecture")
    return interviewer, model_context(case["snapshot"])


def request_body(case: dict, role: str, model: str, tokens: int, *, shared: bool = False) -> dict:
    interview, evaluation = contexts(case)
    union = {"interview_context": interview, "evaluation_context": evaluation}
    if role == "joint":
        payload, schema, tools, prompt = (
            union,
            JointResult,
            INTERVIEWER_TOOL_SPECS + EVALUATOR_TOOL_SPECS,
            JOINT_PROMPT,
        )
    elif role == "interviewer":
        payload, schema, tools, prompt = interview, QuestionDecision, INTERVIEWER_TOOL_SPECS, INTERVIEWER_V3
    else:
        payload, schema, tools, prompt = evaluation, AssessmentResult, EVALUATOR_TOOL_SPECS, EVALUATOR_V3
    # Optional two-call control: same prompts, tools, schemas, concurrency and tokens;
    # only add the other role's input. Never inject a fabricated score or impression.
    if shared and role != "joint":
        payload = {**payload, "other_role_context": evaluation if role == "interviewer" else interview}
    payload = replace_ids(payload, identifier_map(case["snapshot"]))
    body, _ = build_provider_request(
        role=role,
        input_snapshot=AgentInputSnapshot(id=case["case_id"], role=role, payload=payload),
        output_schema=schema,
        tools=tools,
        model=model,
        prompt_override=prompt,
        max_output_tokens=tokens,
    )
    if role == "joint":
        instructions = json.loads(body["messages"][1]["content"])
        defs = instructions["schema"]["$defs"]
        for field in ("turn_id", "rubric_snapshot_id"):
            defs["AssessmentResult"]["properties"][field]["const"] = payload["evaluation_context"][field]
        defs["AssessmentResult"]["properties"]["dimensions"]["maxItems"] = 1
        for name in ("DimensionAssessment", "VerificationGap"):
            defs[name]["properties"]["skill_id"]["const"] = "req_01"
        body["messages"][1]["content"] = json.dumps(instructions, ensure_ascii=False)
    return body


async def fake_output(case: dict, role: str) -> dict:
    """Joint fixtures deliberately compose fake role outputs; they cannot compare quality."""
    interview, evaluation = contexts(case)

    async def generate(selected: str) -> dict:
        result = await FakeGateway().generate_structured(
            role=selected,
            input_snapshot=AgentInputSnapshot(
                id=case["case_id"],
                role=selected,
                payload=interview if selected == "interviewer" else evaluation,
            ),
            output_schema=QuestionDecision if selected == "interviewer" else AssessmentResult,
            tools=INTERVIEWER_TOOL_SPECS if selected == "interviewer" else EVALUATOR_TOOL_SPECS,
            budget=BudgetContext(owner_id="offline-architecture"),
        )
        return replace_ids(result.data, identifier_map(case["snapshot"]))

    if role == "joint":
        return {"assessment": await generate("evaluator"), "next_question": await generate("interviewer")}
    return await generate(role)


def check_parts(case: dict, assessment: object, question: object) -> tuple[dict, dict]:
    mapping = identifier_map(case["snapshot"])
    provider_snapshot = replace_ids(case["snapshot"], mapping)
    score = {
        "case_id": case["case_id"],
        "group_id": case["group_id"],
        "label": case["label"],
        "raw_output": assessment,
        **citation_stats(assessment, provider_snapshot),
    }
    next_q = {"raw_output": question}
    try:
        score.update(check_result(assessment, provider_snapshot))
        score["cleaned"] = restore_ids(score["cleaned"], case["snapshot"])
        score["status"] = "ok"
    except Exception as exc:
        score["status"] = type(exc).__name__
    try:
        parsed = materialize_question_decision(question, replace_ids(interview_snapshot(case), mapping))
        next_q.update(status="ok", cleaned=restore_ids(parsed.model_dump(mode="json"), case["snapshot"]))
    except Exception as exc:
        next_q["status"] = type(exc).__name__
    return score, next_q


def arms_for(args: argparse.Namespace) -> tuple[str, ...]:
    return ARMS + (("two_call_shared",) if args.include_shared else ())


def experiment_plan(args: argparse.Namespace) -> dict:
    cases, version = select_cases(args, planning=True)
    arms = arms_for(args)
    return {
        "experiment_version": VERSION,
        "dataset_version": version,
        "split": args.split,
        "case_count": len(cases),
        "root_question_groups": len({c["group_id"] for c in cases}),
        "human_confirmed": sum(c["label"]["confirmed"] for c in cases),
        "arms": list(arms),
        "repeats": args.repeats,
        "planned_requests": len(cases) * args.repeats * (3 + 2 * args.include_shared),
        "output_token_caps_per_case": {a: args.total_output_tokens for a in arms},
        "api_called": False,
        "scope": (
            "Same answered turn; question proposal and scoring only. No full-interview or training claim."
        ),
    }


async def run_experiment(args: argparse.Namespace) -> dict:
    cases, dataset_version = select_cases(args)
    planned = experiment_plan(args)["planned_requests"]
    if args.mode == "live" and (
        not settings.model_api_key or args.max_requests is None or args.max_requests < planned
    ):
        raise ValueError(f"live requires an API key and --max-requests >= {planned}; no fake fallback")
    model = settings.model_identifier if args.mode == "live" else "fake-v1"
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{args.mode}_{uuid.uuid4().hex[:8]}"
    out = args.output_dir / run_id
    out.mkdir(parents=True)
    arms = arms_for(args)
    inputs = [{**c, "interview_snapshot": interview_snapshot(c)} for c in cases]
    metadata = {
        **experiment_plan(args),
        "run_id": run_id,
        "mode": args.mode,
        "model": model,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "dataset_version": dataset_version,
        "inputs_sha256": digest(inputs),
        "seed": args.seed,
        "temperature": 0.2,
        "api_called": args.mode == "live",
        "endpoint_sha256": digest(settings.model_api_base) if args.mode == "live" else None,
        "prompts": {"joint": JOINT_PROMPT, "interviewer": INTERVIEWER_V3, "evaluator": EVALUATOR_V3},
        "all_labels_human_confirmed": all(c["label"]["confirmed"] for c in cases),
        "limited_selection": args.limit is not None,
        "scheduling": "random arm order; isolated/shared role requests run concurrently",
        "fallback_policy": "No repair or fallback in any arm; invalid proposals remain failures.",
        "review_required": True,
        "actual_cost": None,
    }
    root = Path(__file__).resolve().parents[1]
    metadata["pipeline_sha256"] = digest(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                Path(__file__).resolve(),
                root / "evals/evaluator_quality.py",
                root / "evals/agent_suite.py",
                root / "evals/quality_cases.py",
                root / "evals/identifiers.py",
            ]
            + sorted((root / "packages/agents").glob("*.py"))
            + sorted((root / "packages/domain").glob("*.py"))
            + sorted((root / "packages/llm_gateway").glob("*.py"))
        }
    )
    write_json(out / "inputs.json", inputs)
    write_json(out / "metadata.json", metadata)
    rows = []
    rng = random.Random(args.seed)
    async with httpx.AsyncClient(timeout=45, transport=httpx.AsyncHTTPTransport(retries=0)) as client:

        async def invoke(case: dict, role: str, arm: str) -> dict:
            cap = args.total_output_tokens if role == "joint" else args.total_output_tokens // 2
            body = request_body(case, role, model, cap, shared=arm == "two_call_shared")
            record = {"role": role, "request_body": body, "status": "pending"}
            started = time.monotonic()
            try:
                record["raw_output"] = (
                    await call_live(client, body, record)
                    if args.mode == "live"
                    else await fake_output(case, role)
                )
                record["status"] = "ok"
            except Exception as exc:
                record["status"] = type(exc).__name__
            record["latency_seconds"] = time.monotonic() - started
            return record

        with (out / "results.jsonl").open("w", encoding="utf-8") as handle:
            for repeat in range(1, args.repeats + 1):
                ordered = list(cases)
                rng.shuffle(ordered)
                for case in ordered:
                    arm_order = list(arms)
                    rng.shuffle(arm_order)
                    for arm in arm_order:
                        started = time.monotonic()
                        envelope_ok = True
                        if arm == "single_joint":
                            calls = [await invoke(case, "joint", arm)]
                            raw = calls[0].get("raw_output")
                            envelope_ok = isinstance(raw, dict) and set(raw) == {
                                "assessment",
                                "next_question",
                            }
                            assessment = raw.get("assessment") if envelope_ok else None
                            question = raw.get("next_question") if envelope_ok else None
                        else:
                            calls = list(
                                await asyncio.gather(
                                    invoke(case, "evaluator", arm),
                                    invoke(case, "interviewer", arm),
                                )
                            )
                            assessment, question = (c.get("raw_output") for c in calls)
                        latency = time.monotonic() - started
                        score, next_q = check_parts(case, assessment, question)
                        if arm == "single_joint":
                            if calls[0]["status"] != "ok":
                                score["status"] = next_q["status"] = calls[0]["status"]
                            elif not envelope_ok:
                                score["status"] = next_q["status"] = "InvalidJointEnvelope"
                        else:
                            for task, call in zip((score, next_q), calls, strict=True):
                                if call["status"] != "ok":
                                    task["status"] = call["status"]
                        score["repeat"] = repeat
                        row = {
                            "case_id": case["case_id"],
                            "group_id": case["group_id"],
                            "repeat": repeat,
                            "arm": arm,
                            "calls": calls,
                            "joint_envelope_valid": envelope_ok if arm == "single_joint" else None,
                            "assessment": score,
                            "question": next_q,
                            "wall_seconds": latency,
                        }
                        rows.append(row)
                        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                        handle.flush()
                        print(
                            json.dumps(
                                {
                                    "case_id": case["case_id"],
                                    "repeat": repeat,
                                    "arm": arm,
                                    "score": score["status"],
                                    "question": next_q["status"],
                                }
                            ),
                            flush=True,
                        )
    metadata["results_sha256"] = digest(rows)
    metadata["returned_models"] = sorted(
        {c["returned_model"] for r in rows for c in r["calls"] if c.get("returned_model")}
    )
    report = {
        "metadata": metadata,
        "by_arm": {a: summarize_arm([r for r in rows if r["arm"] == a]) for a in arms},
        "by_group": {
            group: {
                a: summarize_arm([r for r in rows if r["arm"] == a and r["group_id"] == group]) for a in arms
            }
            for group in sorted({r["group_id"] for r in rows})
        },
    }
    write_json(out / "metadata.json", metadata)
    write_json(out / "report.json", report)
    export_review(out, inputs, rows, args.seed)
    return {
        "run_dir": str(out.resolve()),
        "planned_requests": planned,
        "mode": args.mode,
        "next_step": (
            "Fill blind_review.csv then run review. Fake/draft results cannot support quality claims."
        ),
    }


def summarize_arm(rows: list[dict]) -> dict:
    calls = [c for row in rows for c in row["calls"]]
    latencies = sorted(r["wall_seconds"] for r in rows)
    return {
        "scoring": summarize([r["assessment"] for r in rows]),
        "scoring_repeat_consistency": repeat_consistency([r["assessment"] for r in rows]),
        "question_contract_valid": fraction(sum(r["question"]["status"] == "ok" for r in rows), len(rows)),
        "both_tasks_valid": fraction(
            sum(r["question"]["status"] == r["assessment"]["status"] == "ok" for r in rows), len(rows)
        ),
        "question_failures": dict(
            Counter(r["question"]["status"] for r in rows if r["question"]["status"] != "ok")
        ),
        "requests": len(calls),
        "usage": {
            k: sum((c.get("usage") or {}).get(k, 0) or 0 for c in calls)
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "requests_with_usage": sum(bool(c.get("usage")) for c in calls),
        "wall_seconds_mean": sum(latencies) / len(latencies),
        "wall_seconds_p95_nearest_rank": latencies[max(0, (95 * len(latencies) + 99) // 100 - 1)],
        "call_seconds_sum": sum(c["latency_seconds"] for c in calls),
        "actual_cost": None,
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


RATING_FIELDS = ("relevance", "information_gain", "non_repetition", "decision_appropriate")


def review_rows(inputs: list[dict], results: list[dict], seed: int) -> tuple[list[dict], dict]:
    pairs, key = [], {}
    rng = random.Random(seed + 937)
    by_id = {c["case_id"]: c for c in inputs}
    for case_id, repeat in sorted({(r["case_id"], r["repeat"]) for r in results}):
        outputs = {r["arm"]: r for r in results if r["case_id"] == case_id and r["repeat"] == repeat}
        case = by_id[case_id]
        pair_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{seed}:{case_id}:{repeat}").hex[:16]
        names = list(ARMS)
        rng.shuffle(names)
        key[pair_id] = dict(zip(("A", "B"), names, strict=True))
        worksheet = {
            "pair_id": pair_id,
            "case_id": case_id,
            "repeat": repeat,
            "resume_claims": json.dumps(case["interview_snapshot"]["claims"], ensure_ascii=False),
            "requirements": json.dumps(case["interview_snapshot"]["requirements"], ensure_ascii=False),
            "question": case["snapshot"]["question_text"],
            "answer": case["snapshot"]["answer_text"],
            "remaining_question_budget": 5,
        }
        for blind, arm in key[pair_id].items():
            question = outputs[arm]["question"]
            worksheet[f"{blind}_output"] = json.dumps(
                question.get("cleaned") if question["status"] == "ok" else {"status": "FAILED"},
                ensure_ascii=False,
            )
        for blind in ("A", "B"):
            worksheet.update({f"{blind}_{name}": "" for name in RATING_FIELDS})
        worksheet.update(reviewer="", notes="")
        pairs.append(worksheet)
    return pairs, key


def export_review(directory: Path, inputs: list[dict], rows: list[dict], seed: int) -> None:
    pairs, key = review_rows(inputs, rows, seed)
    with (directory / "blind_review.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(pairs[0]))
        writer.writeheader()
        writer.writerows(pairs)
    write_json(directory / "blind_key.json", key)
    write_json(directory / "blind_review_frozen.json", pairs)


def summarize_review(directory: Path) -> dict:
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    metadata = report["metadata"]
    inputs = json.loads((directory / "inputs.json").read_text(encoding="utf-8"))
    results = [
        json.loads(line) for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    if digest(inputs) != metadata["inputs_sha256"] or digest(results) != metadata["results_sha256"]:
        raise ValueError("frozen inputs/results changed")
    expected, expected_key = review_rows(inputs, results, metadata["seed"])
    if json.loads((directory / "blind_key.json").read_text(encoding="utf-8")) != expected_key:
        raise ValueError("blind key changed")
    with (directory / "blind_review.csv").open(newline="", encoding="utf-8-sig") as handle:
        actual = list(csv.DictReader(handle))
    frozen = {r["pair_id"]: r for r in expected}
    if len(actual) != len(frozen) or {r["pair_id"] for r in actual} != set(frozen):
        raise ValueError("review IDs must match all pairs exactly once")
    editable = {"reviewer", "notes"} | {f"{b}_{n}" for b in ("A", "B") for n in RATING_FIELDS}
    wins = Counter()
    totals = {a: [] for a in ARMS}
    reviewers = set()
    for row in actual:
        base = frozen[row["pair_id"]]
        if any(str(base[k]) != row.get(k) for k in base if k not in editable):
            raise ValueError("frozen review evidence changed; edit rating/reviewer/notes columns only")
        values = [row[f"{b}_{n}"].strip() for b in ("A", "B") for n in RATING_FIELDS]
        if not any(values):
            continue
        if (
            any(v not in {"0", "1", "2"} for v in values)
            or not row["reviewer"].strip()
            or not row["notes"].strip()
        ):
            raise ValueError("complete each pair with eight 0/1/2 ratings, reviewer and notes")
        reviewers.add(row["reviewer"].strip())
        scores = {}
        for blind, arm in expected_key[row["pair_id"]].items():
            ratings = [int(row[f"{blind}_{n}"]) for n in RATING_FIELDS]
            if json.loads(row[f"{blind}_output"]) == {"status": "FAILED"} and any(ratings):
                raise ValueError("invalid model output must receive four zero ratings")
            scores[arm] = sum(ratings)
            totals[arm].append(scores[arm])
        diff = scores["multi_isolated"] - scores["single_joint"]
        wins["multi_win" if diff > 0 else "single_win" if diff < 0 else "tie"] += 1
    reviewed, planned = sum(wins.values()), len(actual)
    summary = {
        "run_id": metadata["run_id"],
        "reviewed_pairs": reviewed,
        "pending_pairs": planned - reviewed,
        "paired_counts": {k: wins[k] for k in ("multi_win", "single_win", "tie")},
        "multi_win_rate_including_ties": fraction(wins["multi_win"], reviewed),
        "mean_question_rating_out_of_8": {a: sum(v) / len(v) if v else None for a, v in totals.items()},
        "reviewers": sorted(reviewers),
        "eligible_for_scoped_live_claim": (
            planned > 0
            and reviewed == planned
            and metadata["mode"] == "live"
            and metadata["all_labels_human_confirmed"]
            and len(metadata["returned_models"]) == 1
            and metadata["split"] == "test"
            and not metadata["limited_selection"]
        ),
        "scope": (
            "Descriptive paired results on synthetic fixed turns. "
            "Repeats/variants are not independent topics."
        ),
    }
    write_json(directory / "review_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        item = sub.add_parser(name)
        item.add_argument("--data-dir", type=Path, default=DEFAULT_DATA)
        item.add_argument("--split", choices=("dev", "test", "all"), default="dev")
        item.add_argument("--limit", type=int)
        item.add_argument("--repeats", type=int, default=1)
        item.add_argument("--total-output-tokens", type=int, default=3600)
        item.add_argument(
            "--include-shared", action="store_true", help="add two-call shared-context ablation"
        )
        item.add_argument("--allow-draft", action="store_true")
        if name == "run":
            item.add_argument("--mode", choices=("fake", "live"), default="fake")
            item.add_argument("--max-requests", type=int)
            item.add_argument("--seed", type=int, default=20261022)
            item.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "architecture_runs")
    review = sub.add_parser("review")
    review.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command in {"plan", "run"} and (
        args.repeats < 1
        or args.total_output_tokens < 2
        or args.total_output_tokens % 2
        or (args.limit is not None and args.limit < 1)
    ):
        parser.error("repeats/limit must be positive; total-output-tokens must be a positive even integer")
    try:
        result = (
            summarize_review(args.run_dir)
            if args.command == "review"
            else (experiment_plan(args) if args.command == "plan" else asyncio.run(run_experiment(args)))
        )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
