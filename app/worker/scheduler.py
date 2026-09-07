"""
Source scheduling (spec #33-34).

`due_sources` finds enabled sources whose `frequency` window has elapsed
since `last_run_at`. `trigger_run` enqueues a `collect_source` job for one
source; it is used both by the scheduler tick and by the API's
"run now" endpoint (spec #33: "provide a 'run now' backend operation").

Duplicate-run protection (spec #34) comes for free from the job queue's
idempotent `dedupe_key` (app/worker/job_queue.py): a stable key of
`collect_source:{source_id}` means a second trigger while a job is still
queued/running for that source is a no-op, returning the existing job
rather than creating a duplicate.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import DiscoveryJob
from app.models.source import DiscoverySource
from app.worker.job_queue import enqueue

_FREQUENCY_WINDOWS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


def is_due(source: DiscoverySource, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if not source.enabled:
        return False
    if source.last_run_at is None:
        return True
    window = _FREQUENCY_WINDOWS.get(source.frequency, timedelta(days=1))
    return now - source.last_run_at >= window


def due_sources(db: Session, *, now: datetime | None = None) -> list[DiscoverySource]:
    sources = db.execute(select(DiscoverySource).where(DiscoverySource.enabled.is_(True))).scalars().all()
    return [s for s in sources if is_due(s, now=now)]


def trigger_run(db: Session, source: DiscoverySource, *, historical_import: bool = False) -> DiscoveryJob:
    return enqueue(
        db,
        job_type="collect_source",
        payload={"source_id": str(source.id), "historical_import": historical_import},
        dedupe_key=f"collect_source:{source.id}",
    )


def run_scheduler_tick(db: Session) -> list[DiscoveryJob]:
    """Called periodically (e.g. by a cron-triggered maintenance job, or a
    simple `while True: tick(); sleep(60)` loop run alongside the worker)."""
    jobs = []
    for source in due_sources(db):
        jobs.append(trigger_run(db, source))
    if jobs:
        db.commit()
    return jobs
