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
from datetime import datetime, timezone

from app.config import get_settings
from app.database import session_scope
from app.logging_config import configure_logging, get_logger
from app.services.taxonomy_service import seed_default_taxonomy
from app.worker.handlers import DISPATCH
from app.worker.ist_scheduler import most_recent_cycle_at, seconds_until_next_cycle
from app.worker.job_queue import claim_next_job, complete_job, fail_job, recover_stuck_jobs
from app.worker.scheduler import run_scheduler_tick

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
            # CMT-specific overhaul, Phase 1: same startup seed as
            # app/main.py -- idempotent, so safe here too regardless of
            # which process (API or worker) starts first. Same corrective
            # prompt #13 distinction as app/main.py: a genuinely missing
            # table (e.g. worker started before `alembic upgrade head`) is
            # logged quietly and doesn't crash the worker loop, but any
            # other failure is a real production problem and is logged at
            # ERROR with the full exception, not hidden behind a generic
            # warning.
            try:
                seeded = seed_default_taxonomy(db)
                if seeded:
                    logger.info("taxonomy_seeded_on_startup", inserted=seeded, worker_id=self.worker_id)
            except Exception as exc:  # noqa: BLE001
                is_missing_table = "no such table" in str(exc).lower() or "does not exist" in str(exc).lower()
                if is_missing_table:
                    logger.warning(
                        "taxonomy_seed_on_startup_skipped_missing_table", error=str(exc), worker_id=self.worker_id
                    )
                else:
                    logger.error(
                        "taxonomy_seed_on_startup_failed", error=str(exc), worker_id=self.worker_id, exc_info=True
                    )

        scheduler_task: asyncio.Task | None = None
        if self.settings.scheduler_enabled:
            # "The existing run_scheduler_tick() logic must be connected
            # to a real recurring execution mechanism... no manual API
            # call should be required." This starts that mechanism
            # automatically as part of normal worker startup -- nothing
            # outside this process needs to call anything.
            #
            # FINAL FOCUSED CORRECTION, TASK 3: "resume scheduler
            # operation after restart" is satisfied by an immediate
            # catch-up tick BEFORE entering the recurring loop --
            # `run_scheduler_tick(db)` with no `current_cycle` override
            # resolves to "the most recent 06:00 IST cycle relative to
            # right now", which correctly DETECTS a missed cycle: if the
            # worker was down straight through 06:00 (crash, redeploy,
            # VPS reboot) and restarts at, say, 10:00, that resolves to
            # TODAY's still-current 06:00 cycle, and any source not yet
            # stamped `last_scheduled_cycle_at` for it is correctly due.
            # Critically, this is also a safe NO-OP, not "a fresh normal
            # scheduler run": if today's cycle was already fully
            # processed before this restart (every enabled source
            # already stamped for it), `is_due` returns False for all of
            # them and nothing is (re-)enqueued -- see
            # app/worker/scheduler.py's `is_due`/`run_scheduler_tick` for
            # exactly how `last_scheduled_cycle_at` makes this idempotent
            # regardless of how many times this fires within one cycle.
            with session_scope() as db:
                catchup_jobs = run_scheduler_tick(db)
            logger.info(
                "scheduler_startup_catchup_tick", worker_id=self.worker_id, jobs_enqueued=len(catchup_jobs)
            )
            scheduler_task = asyncio.create_task(self._run_scheduler_loop())

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

        if scheduler_task is not None:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass

        logger.info("worker_stopped", worker_id=self.worker_id)

    async def _run_scheduler_loop(self) -> None:
        """
        The actual recurring-execution mechanism for TASK 4/5/6: sleeps
        until the next 06:00 Asia/Kolkata cycle, fires one global tick,
        then repeats -- for as long as the worker process runs. Restarting
        the worker (crash, redeploy, `docker compose restart`, VPS
        reboot) simply re-enters `run_forever()`, which re-derives the
        next cycle time fresh and starts this loop again -- there is no
        separate scheduling process or external cron to fail to restart.

        The sleep is done in short increments (capped at 60s) rather than
        one long `asyncio.sleep(big_number)` so that a shutdown request
        is noticed promptly instead of after up to 24h.

        Any exception from a tick (a bug, a transient DB outage, etc.) is
        caught, logged with full context, and the loop simply continues
        to the next cycle -- TASK 8: "Scheduler errors should... not
        terminate the worker... not prevent other sources from being
        scheduled" applies to the loop itself, not just to individual
        sources within one tick (per-source isolation is handled inside
        `run_scheduler_tick` itself).
        """
        tz_kwargs = dict(
            hour=self.settings.scheduler_hour,
            minute=self.settings.scheduler_minute,
            tz_name=self.settings.scheduler_timezone,
        )
        logger.info(
            "scheduler_loop_started",
            worker_id=self.worker_id,
            hour=tz_kwargs["hour"],
            minute=tz_kwargs["minute"],
            timezone=tz_kwargs["tz_name"],
        )
        while not self._shutdown.is_set():
            now = datetime.now(timezone.utc)
            remaining = seconds_until_next_cycle(now, **tz_kwargs)
            while remaining > 0 and not self._shutdown.is_set():
                await asyncio.sleep(min(60.0, remaining))
                now = datetime.now(timezone.utc)
                remaining = seconds_until_next_cycle(now, **tz_kwargs)

            if self._shutdown.is_set():
                break

            cycle_at = most_recent_cycle_at(datetime.now(timezone.utc), **tz_kwargs)
            try:
                with session_scope() as db:
                    jobs = run_scheduler_tick(db, current_cycle=cycle_at)
                logger.info(
                    "scheduler_tick_fired",
                    worker_id=self.worker_id,
                    cycle_at=cycle_at.isoformat(),
                    jobs_enqueued=len(jobs),
                )
            except Exception as exc:  # noqa: BLE001 - the recurring loop must never die
                logger.error(
                    "scheduler_tick_failed",
                    worker_id=self.worker_id,
                    cycle_at=cycle_at.isoformat(),
                    error=str(exc),
                    exc_info=True,
                )
            # Loop back around: the top of the next iteration recomputes
            # `seconds_until_next_cycle` from a fresh `now`, which will
            # correctly resolve to ~24h away rather than firing twice for
            # the same cycle.

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
