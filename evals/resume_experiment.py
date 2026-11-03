"""Resume an interrupted live experiment by replaying recorded responses locally.

Completed rows are preserved byte-for-value in the new run. Only unrecorded requests
reach the provider. The source run stays unchanged; provenance records reuse explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path

import httpx

from db.settings import settings
from evals import architecture, evaluator_quality
from evals.evaluator_quality import digest


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def calls_in(rows: list[dict], kind: str) -> list[dict]:
    return [call for row in rows for call in row["calls"]] if kind == "architecture" else rows


def current_pipeline_hash(kind: str) -> str:
    root = Path(__file__).resolve().parents[1]
    if kind == "architecture":
        paths = (
            [
                root / "evals/architecture.py",
                root / "evals/evaluator_quality.py",
                root / "evals/agent_suite.py",
                root / "evals/quality_cases.py",
                root / "evals/identifiers.py",
            ]
            + sorted((root / "packages/agents").glob("*.py"))
            + sorted((root / "packages/domain").glob("*.py"))
            + sorted((root / "packages/llm_gateway").glob("*.py"))
        )
        return digest({str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    production = digest(
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
    return digest(
        {
            "production": production,
            "eval_identifiers": hashlib.sha256((root / "evals/identifiers.py").read_bytes()).hexdigest(),
            "eval_runner": hashlib.sha256((root / "evals/evaluator_quality.py").read_bytes()).hexdigest(),
        }
    )


def restored_report(report: dict, rows: list[dict], kind: str, inputs: list[dict]) -> dict:
    if kind == "architecture":
        report["by_arm"] = {
            arm: architecture.summarize_arm([r for r in rows if r["arm"] == arm]) for arm in report["by_arm"]
        }
        report["by_group"] = {
            group: {
                arm: architecture.summarize_arm(
                    [r for r in rows if r["arm"] == arm and r["group_id"] == group]
                )
                for arm in report["by_arm"]
            }
            for group in sorted({r["group_id"] for r in rows})
        }
    else:
        report["metrics"] = evaluator_quality.summarize(rows)
        report["repeat_consistency"] = evaluator_quality.repeat_consistency(rows)
        report["by_group"] = {
            group: evaluator_quality.summarize([r for r in rows if r["group_id"] == group])
            for group in sorted({r["group_id"] for r in rows})
        }
        report["latency_seconds_mean"] = sum(r["latency_seconds"] for r in rows) / len(rows)
        # All model content, labels, usage and IDs are unchanged by replay; only the
        # measured replay latency is replaced with the original live measurement.
    return report


async def resume(kind: str, source: Path, data: Path, output: Path) -> dict:
    metadata = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    if (source / "report.json").exists():
        raise ValueError("source already has a completed report; do not run it again")
    if metadata["mode"] != "live" or metadata["model"] != settings.model_identifier:
        raise ValueError("resume requires the original live model configuration")
    if metadata["pipeline_sha256"] != current_pipeline_hash(kind):
        raise ValueError("pipeline changed; do not spend requests on an incompatible resumption")
    endpoint = digest(settings.model_api_base or "https://api.openai.com/v1")
    recorded_endpoint = metadata.get("endpoint_sha256", metadata.get("provider_endpoint_sha256"))
    if endpoint != recorded_endpoint:
        raise ValueError("provider endpoint changed")
    old_rows = load_rows(source / "results.jsonl")
    old_calls = calls_in(old_rows, kind)
    if not old_rows or any(c["status"] != "ok" or "raw_content" not in c for c in old_calls):
        raise ValueError("this replay supports successful recorded calls only; preserve failures separately")
    cache = defaultdict(deque)
    for call in old_calls:
        cache[digest(call["request_body"])].append(call)
    planned = metadata.get("planned_requests")
    if planned is None:
        raise ValueError("missing original call plan")
    remaining = planned - len(old_calls)
    if remaining <= 0:
        raise ValueError("no missing requests")
    original_client = httpx.AsyncClient
    observed = {"replayed": 0, "new_requests": 0}

    class ReplayClient(original_client):
        async def post(self, url, **kwargs):
            body = kwargs.get("json")
            key = digest(body)
            if cache[key]:
                call = cache[key].popleft()
                observed["replayed"] += 1
                request_id = call.get("provider_request_id") or "replayed-recorded-response"
                return httpx.Response(
                    call.get("http_status", 200),
                    request=httpx.Request("POST", url),
                    headers={"x-request-id": request_id},
                    json={
                        "id": request_id,
                        "model": call.get("returned_model"),
                        "usage": call.get("usage"),
                        "choices": [{"message": {"content": call["raw_content"]}}],
                    },
                )
            if observed["new_requests"] >= remaining:
                raise RuntimeError("resume request cap reached")
            observed["new_requests"] += 1
            return await super().post(url, **kwargs)

    common = dict(
        data_dir=data,
        output_dir=output,
        mode="live",
        split=metadata["split"],
        limit=None,
        repeats=metadata["repeats"],
        allow_draft=True,
        max_requests=planned,
    )
    selected, _ = evaluator_quality.load_cases(data, metadata["split"], True, None)
    if kind == "architecture":
        selected = [c for c in selected if c["case_id"] in {f"{c['group_id']}_1", f"{c['group_id']}_3"}]
        current_inputs = [{**c, "interview_snapshot": architecture.interview_snapshot(c)} for c in selected]
        expected_inputs_hash = metadata["inputs_sha256"]
    else:
        current_inputs = selected
        expected_inputs_hash = metadata["dataset_sha256"]
    if digest(current_inputs) != expected_inputs_hash:
        raise ValueError("frozen inputs or labels changed")
    httpx.AsyncClient = ReplayClient
    try:
        if kind == "architecture":
            args = argparse.Namespace(
                **common,
                total_output_tokens=metadata["output_token_caps_per_case"]["single_joint"],
                include_shared="two_call_shared" in metadata["arms"],
                seed=metadata["seed"],
            )
            current_prompt = {
                "joint": architecture.JOINT_PROMPT,
                "interviewer": architecture.INTERVIEWER_V3,
                "evaluator": architecture.EVALUATOR_V3,
            }
            if metadata["prompts"] != current_prompt:
                raise ValueError("prompts changed since interrupted run")
            result = await architecture.run_experiment(args)
            directory = Path(result["run_dir"])
        else:
            args = argparse.Namespace(
                **common, max_output_tokens=metadata["max_output_tokens"], prompt_file=None
            )
            if metadata["prompt_text"] != evaluator_quality.EVALUATOR_V3:
                raise ValueError("prompt changed since interrupted run")
            result = await evaluator_quality.run(args)
            directory = Path(result["report"]).parent
    finally:
        httpx.AsyncClient = original_client
    rows = load_rows(directory / "results.jsonl")
    key_fields = ("case_id", "repeat", "arm") if kind == "architecture" else ("case_id", "repeat")
    if len(rows) < len(old_rows) or any(
        tuple(new[k] for k in key_fields) != tuple(old[k] for k in key_fields)
        for new, old in zip(rows, old_rows)
    ):
        raise ValueError("original row order changed; cannot restore the original measurements")
    for new, old in zip(calls_in(rows[: len(old_rows)], kind), old_calls, strict=True):
        if new.get("raw_output") != old.get("raw_output") or new["request_body"] != old["request_body"]:
            raise ValueError("replayed content or request changed")
    if observed["replayed"] != len(old_calls) or any(cache.values()):
        raise ValueError("not all original calls were replayed exactly once")
    rows[: len(old_rows)] = old_rows
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    old_fingerprint = metadata.get("comparison_fingerprint", metadata.get("inputs_sha256"))
    new_fingerprint = report["metadata"].get(
        "comparison_fingerprint", report["metadata"].get("inputs_sha256")
    )
    if (
        old_fingerprint != new_fingerprint
        or metadata["pipeline_sha256"] != report["metadata"]["pipeline_sha256"]
    ):
        raise ValueError("original input/pipeline fingerprint changed")
    report = restored_report(report, rows, kind, json.loads((directory / "inputs.json").read_text()))
    provenance = {
        "source_run": str(source.resolve()),
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "reused_recorded_requests": observed["replayed"],
        "new_requests": observed["new_requests"],
        "original_rows_preserved": len(old_rows),
        "source_results_sha256": hashlib.sha256((source / "results.jsonl").read_bytes()).hexdigest(),
        "resume_driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "unrecorded_requests_at_interruption": "unknown; provider may have processed in-flight requests",
    }
    report["metadata"]["resumption"] = provenance
    report["metadata"]["results_sha256"] = digest(rows)
    with (directory / "results.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    architecture.write_json(directory / "metadata.json", report["metadata"])
    architecture.write_json(directory / "report.json", report)
    architecture.write_json(directory / "resumption.json", provenance)
    return {"run_dir": str(directory.resolve()), **observed, "source_rows_preserved": len(old_rows)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("architecture", "grading"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(resume(args.kind, args.source, args.data_dir, args.output_dir)), indent=2))


if __name__ == "__main__":
    main()
