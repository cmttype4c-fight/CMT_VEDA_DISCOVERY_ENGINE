"""
PostgreSQL-backed job queue (spec #3, #31-34, #49).

Claiming uses `SELECT ... FOR UPDATE SKIP LOCKED`, which is safe across
multiple concurrent worker processes on Postgres without Redis or any
other broker. (On SQLite, used only in tests, `FOR UPDATE SKIP LOCKED`
isn't supported; `claim_next_job` degrades to a plain claim under a
single connection, which is fine for single-threaded test execution --
see the dialect check below.)

`enqueue()`'s idempotency (spec #34, #49) is enforced at the database
level by a partial unique index (`uq_jobs_dedupe_key_active`, see
app/models/job.py) on `dedupe_key` scoped to active (`queued`/`running`)
rows, not merely by the check-then-insert logic below -- see that
function's docstring for the concurrency reasoning.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.job import DiscoveryJob


def _find_active_job_by_dedupe_key(db: Session, dedupe_key: str) -> DiscoveryJob | None:
    return db.execute(
        select(DiscoveryJob).where(
            DiscoveryJob.dedupe_key == dedupe_key,
            DiscoveryJob.status.in_(["queued", "running"]),
        )
    ).scalars().first()


def enqueue(
    db: Session,
    *,
    job_type: str,
    payload: dict,
    dedupe_key: str | None = None,
    max_attempts: int | None = None,
    available_at: datetime | None = None,
) -> DiscoveryJob:
    """
    Idempotent enqueue (spec #49): if a queued/running job with the same
    dedupe_key already exists, return it instead of creating a duplicate.

    Concurrency-safe (spec #34): the check-then-insert below is a fast
    path that avoids an unnecessary failed insert in the common,
    non-racing case, but it is NOT what actually prevents a duplicate --
    two simultaneous callers could both pass the check before either has
    inserted. The real guarantee is the partial unique index
    `uq_jobs_dedupe_key_active` on `discovery_jobs (dedupe_key)` WHERE
    `status IN ('queued', 'running')` (see app/models/job.py). If a race
    is lost, the insert raises IntegrityError; this function catches
    that, re-queries for the row that won, and returns it -- so callers
    always get back *some* active job for that dedupe_key, never an
    exception, exactly as if they'd simply lost the race gracefully.

    The insert itself happens inside its own SAVEPOINT
    (`db.begin_nested()`), not a plain `db.flush()`/`db.rollback()`. This
    makes `enqueue()` safe to call from inside a caller's own nested
    transaction -- e.g. the worker's per-record SAVEPOINT in
    app/worker/handlers.py -- since only this function's own SAVEPOINT is
    rolled back on conflict, never the caller's.
    """
    settings = get_settings()

    if dedupe_key:
        existing = _find_active_job_by_dedupe_key(db, dedupe_key)
        if existing:
            return existing

    job = DiscoveryJob(
        job_type=job_type,
        status="queued",
        payload=payload,
        dedupe_key=dedupe_key,
        max_attempts=max_attempts or settings.job_max_attempts,
        available_at=available_at or datetime.now(timezone.utc),
    )

    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError:
        if dedupe_key:
            existing = _find_active_job_by_dedupe_key(db, dedupe_key)
            if existing:
                return existing
        raise  # not the dedupe race we expected -- surface it

    return job


def claim_next_job(db: Session, *, worker_id: str, job_types: list[str] | None = None) -> DiscoveryJob | None:
    now = datetime.now(timezone.utc)
    query = select(DiscoveryJob).where(
        DiscoveryJob.status == "queued",
        DiscoveryJob.available_at <= now,
    )
    if job_types:
        query = query.where(DiscoveryJob.job_type.in_(job_types))
    query = query.order_by(DiscoveryJob.available_at.asc()).limit(1)

    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)

    job = db.execute(query).scalars().first()
    if job is None:
        return None

    job.status = "running"
    job.locked_at = now
    job.locked_by = worker_id
    job.started_at = now
    job.attempts += 1
    db.flush()
    return job


def complete_job(db: Session, job: DiscoveryJob) -> None:
    job.status = "completed"
    job.completed_at = datetime.now(timezone.utc)
    job.locked_at = None
    job.locked_by = None
    db.flush()


def fail_job(db: Session, job: DiscoveryJob, error: str) -> None:
    """Transient failure handling with exponential backoff (spec #32).
    Exceeding max_attempts moves the job to `dead_letter` rather than
    retrying forever."""
    settings = get_settings()
    job.last_error = error[:4000]
    job.locked_at = None
    job.locked_by = None

    if job.attempts >= job.max_attempts:
        job.status = "dead_letter"
        job.completed_at = datetime.now(timezone.utc)
    else:
        job.status = "queued"
        backoff_seconds = settings.job_backoff_base_seconds * (2 ** (job.attempts - 1))
        job.available_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
    db.flush()


def recover_stuck_jobs(db: Session, *, stuck_after_minutes: int = 30) -> int:
    """Recovery after a worker crash/restart (spec #31): a job left
    'running' with a stale lock is requeued rather than lost forever."""
    threshold = datetime.now(timezone.utc) - timedelta(minutes=stuck_after_minutes)
    stuck = db.execute(
        select(DiscoveryJob).where(DiscoveryJob.status == "running", DiscoveryJob.locked_at < threshold)
    ).scalars().all()
    for job in stuck:
        job.status = "queued"
        job.available_at = datetime.now(timezone.utc)
        job.locked_at = None
        job.locked_by = None
    if stuck:
        db.flush()
    return len(stuck)
