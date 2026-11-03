import argparse
import json
from pathlib import Path

import httpx
import pytest
from evals import evaluator_quality, resume_experiment

from db.settings import settings


async def test_resume_reuses_recorded_responses_and_preserves_original_latencies(tmp_path, monkeypatch):
    """A mock provider proves replay avoids resending completed requests."""
    monkeypatch.setattr(settings, "model_api_key", "mock-key")
    monkeypatch.setattr(settings, "model_identifier", "mock-model")
    monkeypatch.setattr(settings, "model_api_base", "https://mock.invalid/v1")
    counter = {"requests": 0}

    def provider(request):
        counter["requests"] += 1
        content = json.loads(request.content)
        context = json.loads(content["messages"][1]["content"])["context"]
        answer = context["evidence_answers"][0]
        raw = {
            "turn_id": context["turn_id"],
            "rubric_snapshot_id": context["rubric_snapshot_id"],
            "status": "ASSESSED",
            "dimensions": [
                {
                    "skill_id": "req_01",
                    "level": 2,
                    "evidence_status": "DEMONSTRATED",
                    "answer_evidence": [{"message_id": answer["message_id"], "quote": answer["text"]}],
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "id": f"mock-{counter['requests']}",
                "model": "mock-model",
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
                "choices": [{"message": {"content": json.dumps(raw)}}],
            },
        )

    real_client = httpx.AsyncClient

    class MockClient(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(provider)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", MockClient)
    data = tmp_path / "data"
    evaluator_quality.prepare(data)
    args = argparse.Namespace(
        data_dir=data,
        output_dir=tmp_path / "initial",
        split="dev",
        limit=None,
        allow_draft=True,
        mode="live",
        repeats=1,
        max_requests=34,
        max_output_tokens=1800,
        prompt_file=None,
    )
    result = await evaluator_quality.run(args)
    source = Path(result["report"]).parent
    rows = resume_experiment.load_rows(source / "results.jsonl")
    assert counter["requests"] == 34
    original = rows[:13]
    (source / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in original))
    (source / "report.json").unlink()
    counter["requests"] = 0
    resumed = await resume_experiment.resume("grading", source, data, tmp_path / "resumed")
    destination = Path(resumed["run_dir"])
    new_rows = resume_experiment.load_rows(destination / "results.jsonl")
    assert counter["requests"] == 21
    assert resumed["replayed"] == 13 and resumed["new_requests"] == 21
    assert new_rows[:13] == original
    assert len(new_rows) == 34
    report = json.loads((destination / "report.json").read_text())
    assert report["usage"]["total_tokens"] == 102
    assert report["latency_seconds_mean"] == sum(r["latency_seconds"] for r in new_rows) / 34
    assert report["metadata"]["resumption"]["reused_recorded_requests"] == 13
    assert len(resume_experiment.load_rows(source / "results.jsonl")) == 13
    with pytest.raises(ValueError, match="completed report"):
        await resume_experiment.resume("grading", destination, data, tmp_path / "duplicate")
