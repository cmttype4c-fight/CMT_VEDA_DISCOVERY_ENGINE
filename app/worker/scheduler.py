"""
Source scheduling (spec #33-34).

FINAL FOCUSED CORRECTION -- SCHEDULER, SOURCE IMPLEMENTATION, TESTS &
COMPLETE ZIP, TASK 1/2/3: due-ness is now based on the fixed 06:00
Asia/Kolkata *scheduled cycle* a source last participated in
(`DiscoverySource.last_scheduled_cycle_at`), not on `last_run_at` (a
collection-COMPLETION timestamp). This replaces the previous pass's
elapsed-time-since-completion logic, which had a real bug:

    Monday   06:00 -> daily source scheduled
    Monday   08:00 -> collection finishes, last_run_at = Mon 08:00
    Tuesday  06:00 -> only 22h elapsed since Mon 08:00 -> WRONGLY not due
    Wednesday 06:00 -> 46h elapsed -> runs (a whole day was silently skipped)

`last_scheduled_cycle_at` is stamped with the CYCLE timestamp (always an
exact `ist_scheduler.most_recent_cycle_at()` value, e.g. "2026-09-19
00:30 UTC" = "2026-09-19 06:00 IST"), not real time, and is stamped at
TICK time (when the source is identified as due and its job is
triggered), not at collection-completion time. Two cycle timestamps are
therefore always an exact multiple of 24h apart, so:

    Monday   06:00 -> RUN, last_scheduled_cycle_at = Mon 06:00 IST
    Monday   08:00 -> collection finishes (last_run_at = Mon 08:00, unused for scheduling)
    Tuesday  06:00 -> elapsed since last_scheduled_cycle_at = exactly 24h -> RUN
    Tuesday  09:30 -> collection finishes
    Wednesday 06:00 -> RUN

`last_run_at`/`last_success_at` (set by app/worker/handlers.py on actual
collection completion) are unchanged and keep their existing meaning --
observability/"when did this last actually run", not scheduling input.

`due_sources` finds enabled sources whose `frequency` window has elapsed
since `last_scheduled_cycle_at`, evaluated against a specific
`current_cycle` (always a `most_recent_cycle_at()` boundary -- see
app/worker/ist_scheduler.py). `trigger_run` enqueues a `collect_source`
job for one source; it is used both by the scheduler tick and by the
API's "run now" endpoint (spec #33) -- the manual "run now" path
deliberately does NOT touch `last_scheduled_cycle_at`, since an
admin-triggered run is orthogonal to the scheduled cadence and must not
cause the next 06:00 cycle to think it already ran.

Duplicate-run protection (spec #34, TASK for "duplicate protection")
comes from two layers working together:
  1. The job queue's idempotent `dedupe_key` (app/worker/job_queue.py):
     a stable key of `collect_source:{source_id}` means a second trigger
     while a job is still queued/running for that source is a no-op,
     returning the existing job -- enforced by a DB-level partial unique
     index (`uq_jobs_dedupe_key_active`), safe even across concurrent
     worker replicas/ticks.
  2. `last_scheduled_cycle_at` itself: once a source is stamped for a
     given cycle, `is_due` returns False for it for the REST of that
     cycle even after its job has completed (successfully or not) and
     is no longer "active" -- which is exactly the gap layer 1 alone
     cannot cover (a restart hours after the original job finished would
     otherwise see no active job and could re-trigger a second
     collection for the same already-handled cycle).

Timestamps read from the database may come back timezone-naive (this
codebase's SQLite test backend does this despite `DateTime(timezone=True)`
-- see `ist_scheduler.as_aware_utc`'s docstring). Every DB-sourced
timestamp used in a comparison in this module is passed through
`as_aware_utc` first, so a naive value from SQLite (or, defensively, any
other source) can never raise
`TypeError: can't subtract offset-naive and offset-aware datetimes`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.job import DiscoveryJob
from app.models.source import DiscoverySource
from app.worker.ist_scheduler import as_aware_utc, most_recent_cycle_at, next_cycle_at
from app.worker.job_queue import enqueue

_FREQUENCY_WINDOWS = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


def frequency_window(frequency: str) -> timedelta:
    return _FREQUENCY_WINDOWS.get(frequency, timedelta(days=1))


def _current_cycle_or_now(current_cycle: datetime | None) -> datetime:
    """Resolve the cycle boundary to evaluate against: an explicitly
    passed cycle timestamp, or (default) the most recent 06:00
    Asia/Kolkata boundary at/before real current time."""
    if current_cycle is not None:
        return current_cycle
    return most_recent_cycle_at(datetime.now(timezone.utc))


def is_due(source: DiscoverySource, *, current_cycle: datetime | None = None) -> bool:
    """
    Whether `source` should run at `current_cycle` (defaults to the most
    recent 06:00 Asia/Kolkata boundary). Based entirely on
    `last_scheduled_cycle_at` -- NEVER on `last_run_at`/collection
    completion time -- so how long a previous collection took can never
    shift a source's scheduled cadence. See this module's docstring for
    the worked example.
    """
    current_cycle = _current_cycle_or_now(current_cycle)
    if not source.enabled:
        return False

    last_cycle = as_aware_utc(source.last_scheduled_cycle_at)
    if last_cycle is None:
        return True  # never scheduled -- a first run is always due

    return current_cycle - last_cycle >= frequency_window(source.frequency)


def due_sources(db: Session, *, current_cycle: datetime | None = None) -> list[DiscoverySource]:
    current_cycle = _current_cycle_or_now(current_cycle)
    sources = db.execute(select(DiscoverySource).where(DiscoverySource.enabled.is_(True))).scalars().all()
    return [s for s in sources if is_due(s, current_cycle=current_cycle)]


def estimate_next_due_at(source: DiscoverySource) -> datetime | None:
    """Best-effort "next expected run" for observability (TASK 3/9). This
    is the next CYCLE boundary at/after which the source becomes due --
    an estimate of scheduling eligibility, not a promise of exactly when
    collection will happen (that depends on the recurring loop actually
    ticking; see app/worker/worker.py). Returns `None` for a disabled
    source (it has no next run) and for a source that has never been
    scheduled (it is already due -- "now", not a future time)."""
    if not source.enabled:
        return None
    last_cycle = as_aware_utc(source.last_scheduled_cycle_at)
    if last_cycle is None:
        return None  # already due; there is no meaningful "next" time

    earliest_due_at = last_cycle + frequency_window(source.frequency)
    # For daily/weekly/monthly, `earliest_due_at` is itself always an
    # exact 06:00 IST cycle boundary (both `last_cycle` and the window
    # are whole-day multiples relative to the same fixed anchor) --
    # `most_recent_cycle_at` returns it unchanged in that case. Only for
    # a sub-daily window (e.g. "hourly", which doesn't align to daily
    # cycle boundaries) does it land strictly between two boundaries; in
    # that case, round UP to the next one, since the scheduler only ever
    # actually checks due-ness at real cycle boundaries.
    candidate = most_recent_cycle_at(earliest_due_at)
    return candidate if candidate == earliest_due_at else next_cycle_at(earliest_due_at)


def trigger_run(db: Session, source: DiscoverySource, *, historical_import: bool = False) -> DiscoveryJob:
    """Enqueue a `collect_source` job for `source`. Used by both the
    scheduler (which additionally stamps `last_scheduled_cycle_at` --
    see `run_scheduler_tick`) and the API's manual "run now" endpoint
    (`app/api/routers/sources.py`), which deliberately calls this
    directly and does NOT stamp `last_scheduled_cycle_at`: an
    admin-triggered run is orthogonal to the scheduled cadence and must
    never cause the next 06:00 cycle to believe it already ran."""
    return enqueue(
        db,
        job_type="collect_source",
        payload={"source_id": str(source.id), "historical_import": historical_import},
        dedupe_key=f"collect_source:{source.id}",
    )


def run_scheduler_tick(db: Session, *, current_cycle: datetime | None = None) -> list[DiscoveryJob]:
    """
    Evaluate every enabled source against `current_cycle` (defaults to
    the most recent 06:00 Asia/Kolkata boundary) and enqueue a
    `collect_source` job for each one that is due, stamping
    `last_scheduled_cycle_at = current_cycle` for each source
    considered due -- regardless of whether `trigger_run` created a
    brand-new job or returned an already-active one (TASK: "do not
    create duplicate jobs for the same source + scheduled cycle") --
    so a second tick for the SAME cycle (a near-simultaneous duplicate
    tick, or a restart minutes/hours later within the same cycle window)
    always finds the source no longer due and skips it entirely, with no
    dependency on whether the earlier job is still active.

    Called by the recurring 06:00 Asia/Kolkata loop in
    app/worker/worker.py (the actual automatic-execution mechanism), and
    also by that same worker's startup catch-up tick (no `current_cycle`
    override -- it resolves to "the most recent cycle relative to right
    now", which correctly identifies a MISSED cycle after a restart, per
    TASK 3, while being a safe no-op for any source whose current cycle
    was already processed before the restart).

    A single source's `trigger_run` failing (e.g. an unexpected DB error
    building one job) does not prevent the rest of the tick from
    completing -- each failure is caught, logged with source context,
    and the loop continues; that source is simply left not-yet-stamped
    for this cycle, so it will correctly be retried on the very next
    tick rather than waiting a full frequency window.
    """
    from app.logging_config import get_logger

    logger = get_logger(component="scheduler")
    current_cycle = _current_cycle_or_now(current_cycle)

    jobs = []
    for source in due_sources(db, current_cycle=current_cycle):
        try:
            job = trigger_run(db, source)
            source.last_scheduled_cycle_at = current_cycle
            jobs.append(job)
        except Exception as exc:  # noqa: BLE001 - one source's failure must not block the rest of the tick
            logger.error(
                "scheduler_tick_source_enqueue_failed",
                source_id=str(source.id),
                source_name=source.source_name,
                cycle_at=current_cycle.isoformat(),
                error=str(exc),
            )
    if jobs:
        db.commit()
    return jobs


def scheduler_diagnostics(db: Session, *, now: datetime | None = None, stale_multiplier: float = 2.0) -> dict:
    """
    TASK 9 (previous pass, retained): "Make scheduler state observable...
    distinguishable from collector problems." Computed entirely from the
    existing `discovery_sources` and `discovery_jobs` tables.

    Per source, classifies status as:
      - "disabled": not currently scheduled at all.
      - "collector_error": this source WAS correctly stamped for the
        current cycle (the scheduler picked it up), its most recent
        actual run recorded an error, and nothing new is in flight -- a
        COLLECTOR problem, not a scheduler problem.
      - "possibly_stalled": the source is due (or overdue by more than
        `stale_multiplier` x its own frequency window, measured in
        cycles) and has neither been stamped for a recent cycle nor has
        an active queued/running job -- the signature of the global
        SCHEDULER not ticking at all (every source across every
        frequency would show this simultaneously), as opposed to one
        source's collector failing.
      - "ok": everything else.
    """
    now = now or datetime.now(timezone.utc)
    current_cycle = most_recent_cycle_at(now)

    # Fetched and filtered in Python rather than via a JSON-path query
    # so this stays portable across Postgres (production) and SQLite
    # (tests) -- same portability discipline as PortableJSON.
    active_jobs = db.execute(
        select(DiscoveryJob).where(
            DiscoveryJob.job_type == "collect_source",
            DiscoveryJob.status.in_(["queued", "running"]),
        )
    ).scalars().all()
    active_source_ids = {job.payload.get("source_id") for job in active_jobs if job.payload}

    sources = db.execute(select(DiscoverySource).order_by(DiscoverySource.source_name)).scalars().all()
    rows = []
    for source in sources:
        due = is_due(source, current_cycle=current_cycle)
        has_active_job = str(source.id) in active_source_ids
        window = frequency_window(source.frequency)
        last_cycle = as_aware_utc(source.last_scheduled_cycle_at)
        was_stamped_for_current_cycle = last_cycle is not None and current_cycle - last_cycle < window

        if not source.enabled:
            status = "disabled"
        elif source.last_error and was_stamped_for_current_cycle and not has_active_job:
            status = "collector_error"
        elif due and not has_active_job and (last_cycle is None or current_cycle - last_cycle >= window * stale_multiplier):
            status = "possibly_stalled"
        else:
            status = "ok"

        rows.append(
            {
                "source_id": str(source.id),
                "source_name": source.source_name,
                "enabled": source.enabled,
                "frequency": source.frequency,
                "last_run_at": source.last_run_at,
                "last_success_at": source.last_success_at,
                "last_scheduled_cycle_at": source.last_scheduled_cycle_at,
                "last_error": source.last_error,
                "is_due": due,
                "has_active_job": has_active_job,
                "next_expected_due_at": estimate_next_due_at(source),
                "status": status,
            }
        )

    return {
        "checked_at": now,
        "current_cycle_at": current_cycle,
        "next_scheduler_cycle_at": next_cycle_at(now),
        "sources": rows,
    }
