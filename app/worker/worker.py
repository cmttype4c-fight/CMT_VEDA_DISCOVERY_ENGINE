"""
Discovery Worker main loop (spec #31).

Low initial concurrency (2-3 concurrent job tasks, spec #31) via an
asyncio semaphore. Each job runs in its own DB session/transaction so one
job's failure/rollback never affects another's.

Run with:  python -m app.worker.worker
"""
from __future__ import annotations

import asyncio
import signal
import uuid

from app.config import get_settings
from app.database import session_scope
from app.logging_config import configure_logging, get_logger
from app.worker.handlers import DISPATCH
from app.worker.job_queue import claim_next_job, complete_job, fail_job, recover_stuck_jobs

logger = get_logger(component="worker")


class Worker:
    def __init__(self, worker_id: str | None = None):
        self.settings = get_settings()
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self._shutdown = asyncio.Event()
        self._semaphore = asyncio.Semaphore(self.settings.worker_concurrency)

    def request_shutdown(self, *_args) -> None:
        logger.info("worker_shutdown_requested", worker_id=self.worker_id)
        self._shutdown.set()

    async def run_forever(self) -> None:
        logger.info("worker_started", worker_id=self.worker_id, concurrency=self.settings.worker_concurrency)

        with session_scope() as db:
            recovered = recover_stuck_jobs(db)
            if recovered:
                logger.info("recovered_stuck_jobs", count=recovered)

        tasks: set[asyncio.Task] = set()
        while not self._shutdown.is_set():
            if len(tasks) < self.settings.worker_concurrency:
                job_claimed = await self._try_claim_and_dispatch()
                if job_claimed is not None:
                    tasks.add(job_claimed)
                    job_claimed.add_done_callback(tasks.discard)
                else:
                    await asyncio.sleep(self.settings.worker_poll_interval_seconds)
            else:
                await asyncio.sleep(0.5)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("worker_stopped", worker_id=self.worker_id)

    async def _try_claim_and_dispatch(self) -> asyncio.Task | None:
        with session_scope() as db:
            job = claim_next_job(db, worker_id=self.worker_id)
            if job is None:
                return None
            job_id, job_type, payload = job.id, job.job_type, job.payload

        return asyncio.create_task(self._run_job(job_id))

    async def _run_job(self, job_id) -> None:
        async with self._semaphore:
            handler = None
            with session_scope() as db:
                from app.models.job import DiscoveryJob

                job = db.get(DiscoveryJob, job_id)
                if job is None:
                    return
                handler = DISPATCH.get(job.job_type)
                job_type = job.job_type

            if handler is None:
                with session_scope() as db:
                    job = db.get(DiscoveryJob, job_id)
                    fail_job(db, job, f"No handler registered for job_type={job_type!r}")
                return

            try:
                with session_scope() as db:
                    from app.models.job import DiscoveryJob

                    job = db.get(DiscoveryJob, job_id)
                    result = await handler(db, job)
                    complete_job(db, job)
                logger.info("job_completed", job_id=str(job_id), job_type=job_type, result=result)
            except Exception as exc:  # noqa: BLE001 - must not crash the worker loop
                logger.error("job_failed", job_id=str(job_id), job_type=job_type, error=str(exc))
                with session_scope() as db:
                    from app.models.job import DiscoveryJob

                    job = db.get(DiscoveryJob, job_id)
                    if job is not None:
                        fail_job(db, job, str(exc))


async def _main() -> None:
    configure_logging()
    worker = Worker()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, worker.request_shutdown)
        except NotImplementedError:
            pass  # Windows dev environments
    await worker.run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
