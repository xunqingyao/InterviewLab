import asyncio
import uuid
from types import SimpleNamespace

import pytest

from domain.errors import InvalidAgentOutput
from worker import runner


class StubSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


@pytest.mark.parametrize("batch_size", [1, 2])
@pytest.mark.asyncio
async def test_job_failure_is_isolated_in_single_and_concurrent_batches(monkeypatch, caplog, batch_size):
    jobs = [SimpleNamespace(id=uuid.uuid4(), kind="EVALUATE_TURN") for _ in range(batch_size)]
    queued = iter(jobs)
    attempted = []

    async def claim(*_):
        return next(queued, None)

    async def reclaim(*_):
        return 0

    async def process(_, job, worker_id):
        attempted.append(job.id)
        if job.id == jobs[0].id:
            raise InvalidAgentOutput("private answer text must not enter logs")

    monkeypatch.setattr(runner, "SessionLocal", StubSession)
    monkeypatch.setattr(runner, "claim_one", claim)
    monkeypatch.setattr(runner, "reclaim_expired", reclaim)
    monkeypatch.setattr(runner, "process_job", process)

    assert await runner.drain_jobs(limit=batch_size, worker_id="test-worker") == batch_size
    assert set(attempted) == {job.id for job in jobs}
    assert "worker.job_failed" in caplog.text
    assert "InvalidAgentOutput" in caplog.text
    assert "private answer text" not in caplog.text


@pytest.mark.asyncio
async def test_worker_cancellation_is_not_swallowed(monkeypatch):
    async def cancel(*_):
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "SessionLocal", StubSession)
    monkeypatch.setattr(runner, "process_job", cancel)
    job = SimpleNamespace(id=uuid.uuid4(), kind="EVALUATE_TURN")
    with pytest.raises(asyncio.CancelledError):
        await runner._process(job, "test-worker")
