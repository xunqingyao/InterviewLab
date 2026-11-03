from __future__ import annotations

import asyncio

from db.handlers import process_job
from db.jobs_repo import claim_one, reclaim_expired
from db.session import SessionLocal
from db.settings import settings
from observability.logging import logger


async def _process(job, worker_id: str) -> None:
    try:
        async with SessionLocal() as session:
            await process_job(session, job, worker_id)
    except Exception as exc:
        # process_job persists RETRY_WAIT/DEAD before re-raising. Isolate this task's failure
        # for both single-job and concurrent batches; never log model output or answer text.
        logger.error(
            "worker.job_failed job_id=%s kind=%s error_code=%s",
            job.id,
            job.kind,
            type(exc).__name__,
        )


async def drain_jobs(limit: int = 50, worker_id: str | None = None) -> int:
    worker_id = worker_id or settings.worker_id
    processed = 0
    while processed < limit:
        async with SessionLocal() as session:
            await reclaim_expired(session)
        batch = []
        for _ in range(min(2, limit - processed)):
            async with SessionLocal() as session:
                job = await claim_one(session, worker_id)
            if job is None:
                break
            batch.append(job)
        if not batch:
            break
        if len(batch) == 1:
            await _process(batch[0], worker_id)
        else:
            await asyncio.gather(*[_process(job, worker_id) for job in batch], return_exceptions=True)
        processed += len(batch)
    return processed


async def run_forever() -> None:
    worker_id = settings.worker_id
    logger.info("worker.start", extra={"worker_id": worker_id})
    try:
        from agents.checkpoints import setup_checkpoint_tables

        await setup_checkpoint_tables()
    except Exception:
        logger.info("worker.checkpoint_setup_skipped")
    while True:
        n = await drain_jobs(limit=8, worker_id=worker_id)
        if n == 0:
            await asyncio.sleep(0.5)
