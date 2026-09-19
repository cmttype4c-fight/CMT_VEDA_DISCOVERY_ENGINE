"""
FINAL FOCUSED CORRECTION -- SCHEDULER, SOURCE IMPLEMENTATION, TESTS &
COMPLETE ZIP, TASK 9: "Add/update tests for" the real calendar-cycle
scheduling requirement. Covers every scenario listed by name: fixed time,
daily source (consecutive calendar days despite late completion), weekly,
monthly, restart, duplicate protection, disabled source, first run,
timezone independence, and naive database timestamps.

The core due-logic these tests exercise (`is_due`/`estimate_next_due_at`
in app/worker/scheduler.py) was ALSO genuinely executed directly in the
build sandbox against the exact Monday/Tuesday/Wednesday scenario from
the corrective prompt, by loading the real scheduler.py file via
importlib with lightweight stand-ins for its SQLAlchemy imports (which
this sandbox cannot install) -- see IMPLEMENTATION_STATUS.md for that
run's full output and exactly what it does and doesn't prove. The tests
below that need a real `db_session` (SQLAlchemy `Session`) fixture --
i.e. everything that goes through `due_sources`/`run_scheduler_tick`/
`scheduler_diagnostics` against an actual database -- are, like the rest
of this repository's SQLAlchemy-dependent tests, `py_compile`-checked and
manually traced, not executed by pytest itself (still-uninstallable
fastapi/SQLAlchemy/pytest, reconfirmed again this pass).
"""
from datetime import datetime, timedelta, timezone

from app.models.source import DiscoverySource
from app.worker.ist_scheduler import most_recent_cycle_at
from app.worker.job_queue import claim_next_job
from app.worker.scheduler import (
    due_sources,
    estimate_next_due_at,
    is_due,
    run_scheduler_tick,
    scheduler_diagnostics,
)

MON_0600 = datetime(2026, 1, 12, 0, 30, 0, tzinfo=timezone.utc)  # Monday 06:00 IST
TUE_0600 = MON_0600 + timedelta(days=1)
WED_0600 = MON_0600 + timedelta(days=2)


def _source(db, *, frequency="daily", enabled=True, last_scheduled_cycle_at=None, last_run_at=None, last_error=None):
    source = DiscoverySource(
        source_name=f"Test source ({frequency})",
        source_type="api",
        source_tier="tier_1",
        collection_method="official_api",
        configuration={"collector": "pubmed"},
        frequency=frequency,
        enabled=enabled,
        last_scheduled_cycle_at=last_scheduled_cycle_at,
        last_run_at=last_run_at,
        last_error=last_error,
    )
    db.add(source)
    db.flush()
    return source


# --- Fixed time / the exact Monday-08:00-completion bug scenario (TASK 1/9) ---

def test_daily_source_still_due_tuesday_despite_monday_completion_landing_at_0800():
    """The corrective prompt's own example, reproduced exactly:
    Monday 06:00 scheduled, Monday 08:00 completion -- Tuesday 06:00 MUST
    still be due (elapsed-time-since-COMPLETION would wrongly say only
    22h passed; cycle-based scheduling correctly says exactly 24h passed
    since the last SCHEDULED cycle)."""
    source = DiscoverySource(
        frequency="daily", enabled=True,
        last_scheduled_cycle_at=MON_0600,
        last_run_at=MON_0600 + timedelta(hours=2),  # completed Monday 08:00
    )
    assert is_due(source, current_cycle=TUE_0600) is True


def test_daily_source_still_due_wednesday_despite_tuesday_completion_landing_at_0930():
    source = DiscoverySource(
        frequency="daily", enabled=True,
        last_scheduled_cycle_at=TUE_0600,
        last_run_at=TUE_0600 + timedelta(hours=3, minutes=30),  # completed Tuesday 09:30
    )
    assert is_due(source, current_cycle=WED_0600) is True


def test_daily_source_not_due_again_within_the_same_already_stamped_cycle():
    """A source already stamped for today's cycle must not appear due
    again if `is_due` is re-checked for that SAME cycle (e.g. a second
    tick, or a restart later the same day) -- regardless of whether or
    when its collection actually completed."""
    source = DiscoverySource(
        frequency="daily", enabled=True,
        last_scheduled_cycle_at=TUE_0600,
        last_run_at=TUE_0600 + timedelta(hours=5),
    )
    assert is_due(source, current_cycle=TUE_0600) is False


# --- Daily / weekly / monthly (TASK 2/9) ---

def test_weekly_source_only_due_once_its_7_cycle_interval_has_elapsed():
    not_yet = DiscoverySource(frequency="weekly", enabled=True, last_scheduled_cycle_at=MON_0600)
    due = DiscoverySource(frequency="weekly", enabled=True, last_scheduled_cycle_at=MON_0600)
    assert is_due(not_yet, current_cycle=MON_0600 + timedelta(days=6)) is False
    assert is_due(due, current_cycle=MON_0600 + timedelta(days=7)) is True


def test_monthly_source_only_due_once_its_30_cycle_interval_has_elapsed():
    not_yet = DiscoverySource(frequency="monthly", enabled=True, last_scheduled_cycle_at=MON_0600)
    due = DiscoverySource(frequency="monthly", enabled=True, last_scheduled_cycle_at=MON_0600)
    assert is_due(not_yet, current_cycle=MON_0600 + timedelta(days=29)) is False
    assert is_due(due, current_cycle=MON_0600 + timedelta(days=30)) is True


# --- Disabled source ---

def test_disabled_source_is_never_due_even_if_badly_overdue():
    source = DiscoverySource(frequency="daily", enabled=False, last_scheduled_cycle_at=MON_0600)
    assert is_due(source, current_cycle=MON_0600 + timedelta(days=365)) is False


# --- First run ---

def test_never_scheduled_source_is_due_immediately():
    source = DiscoverySource(frequency="daily", enabled=True, last_scheduled_cycle_at=None)
    assert is_due(source, current_cycle=MON_0600) is True


def test_due_sources_includes_first_run_source_from_db(db_session):
    _source(db_session, last_scheduled_cycle_at=None)
    due = due_sources(db_session, current_cycle=MON_0600)
    assert len(due) == 1


# --- Timezone independence ---

def test_default_current_cycle_resolution_unaffected_by_process_timezone():
    """Changing the host/process timezone must not alter which 06:00
    Asia/Kolkata cycle `due_sources`/`run_scheduler_tick` resolve to when
    no explicit `current_cycle` is passed."""
    import os
    import time

    from app.worker.scheduler import _current_cycle_or_now

    baseline = _current_cycle_or_now(None)
    original_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        assert _current_cycle_or_now(None) == baseline
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


# --- Naive database timestamps (TASK 4/9) ---

def test_is_due_does_not_raise_on_naive_last_scheduled_cycle_at():
    """Simulates SQLite returning a timezone-naive value for a column
    declared `DateTime(timezone=True)` (see
    app/worker/ist_scheduler.py's `as_aware_utc` docstring) -- must not
    raise `TypeError: can't subtract offset-naive and offset-aware
    datetimes`, and must still compute the correct due-ness."""
    naive_stamp = MON_0600.replace(tzinfo=None)
    source = DiscoverySource(frequency="daily", enabled=True, last_scheduled_cycle_at=naive_stamp)
    assert is_due(source, current_cycle=TUE_0600) is True  # does not raise


def test_estimate_next_due_at_does_not_raise_on_naive_last_scheduled_cycle_at():
    naive_stamp = MON_0600.replace(tzinfo=None)
    source = DiscoverySource(frequency="daily", enabled=True, last_scheduled_cycle_at=naive_stamp)
    assert estimate_next_due_at(source) == TUE_0600  # does not raise


# --- Duplicate protection (TASK "duplicate protection") ---

def test_run_scheduler_tick_does_not_double_enqueue_for_same_cycle(db_session):
    """Two ticks for the SAME cycle -- e.g. a near-simultaneous duplicate
    tick, or a restart minutes later within the same cycle window --
    must not create a second job, even after accounting for the fact
    that the source is now correctly stamped `last_scheduled_cycle_at`
    for that cycle (not just relying on the job still being 'active')."""
    _source(db_session, last_scheduled_cycle_at=None)

    first_jobs = run_scheduler_tick(db_session, current_cycle=MON_0600)
    assert len(first_jobs) == 1

    second_jobs = run_scheduler_tick(db_session, current_cycle=MON_0600)
    assert len(second_jobs) == 0  # already stamped for MON_0600 -- correctly not due again


def test_run_scheduler_tick_does_not_double_enqueue_even_after_first_job_completes(db_session):
    """The stronger duplicate-protection case the previous pass's design
    could NOT handle: once the first job for this cycle has already
    completed (so it's no longer 'active' in the job queue), a second
    tick for the SAME cycle must still not enqueue a new one -- because
    `last_scheduled_cycle_at` (not job-queue activity) is what now gates
    scheduling."""
    from app.worker.job_queue import complete_job

    _source(db_session, last_scheduled_cycle_at=None)
    first_jobs = run_scheduler_tick(db_session, current_cycle=MON_0600)
    claimed = claim_next_job(db_session, worker_id="w1")
    complete_job(db_session, claimed)

    second_jobs = run_scheduler_tick(db_session, current_cycle=MON_0600)
    assert len(second_jobs) == 0


def test_run_scheduler_tick_enqueues_again_at_the_genuinely_next_cycle(db_session):
    """Duplicate protection must not become permanent blocking: the
    following day's cycle is a legitimately new one."""
    source = _source(db_session, last_scheduled_cycle_at=None)
    first_jobs = run_scheduler_tick(db_session, current_cycle=MON_0600)

    second_jobs = run_scheduler_tick(db_session, current_cycle=TUE_0600)
    assert len(second_jobs) == 1
    assert second_jobs[0].id != first_jobs[0].id
    assert source.last_scheduled_cycle_at == TUE_0600


# --- Failure recovery ---

def test_failed_collection_does_not_block_next_scheduled_cycle(db_session):
    """'Monday 06:00 -> PubMed fails. Tuesday 06:00 -> PubMed must still
    be eligible to run again.' `is_due` only looks at
    `last_scheduled_cycle_at` (stamped at TICK time, unconditionally) --
    never at `last_error` -- so a failed run's error does not change
    scheduling eligibility for the next cycle."""
    source = _source(
        db_session,
        last_scheduled_cycle_at=MON_0600,  # Monday's cycle was processed...
        last_run_at=MON_0600 + timedelta(hours=2),
        last_error="CollectorError: upstream API returned 503",  # ...and failed
    )
    assert is_due(source, current_cycle=TUE_0600) is True


def test_run_scheduler_tick_one_source_enqueue_failure_does_not_block_others(db_session, monkeypatch):
    """A scheduler-level error for one source must not prevent other
    sources from being scheduled in the same tick, and the failed
    source is correctly left un-stamped so it's retried on the very next
    tick rather than waiting a full frequency window."""
    import app.worker.scheduler as scheduler_module

    good = _source(db_session, last_scheduled_cycle_at=None)
    bad = _source(db_session, last_scheduled_cycle_at=None)

    real_trigger_run = scheduler_module.trigger_run

    def flaky_trigger_run(db, source, **kwargs):
        if source.id == bad.id:
            raise RuntimeError("simulated failure building the job for this one source")
        return real_trigger_run(db, source, **kwargs)

    monkeypatch.setattr(scheduler_module, "trigger_run", flaky_trigger_run)

    jobs = run_scheduler_tick(db_session, current_cycle=MON_0600)
    assert len(jobs) == 1
    assert jobs[0].payload["source_id"] == str(good.id)
    assert good.last_scheduled_cycle_at == MON_0600
    assert bad.last_scheduled_cycle_at is None  # left un-stamped -- retried next tick, not next window


# --- Restart / catch-up idempotency (TASK 3/9) ---

def test_missed_cycle_is_caught_up_after_restart(db_session):
    """Worker was down straight through 06:00 and restarts later the
    same day (e.g. 10:00) -- the catch-up tick (no explicit
    `current_cycle`, resolving to 'the most recent cycle relative to
    right now') must still find and enqueue the missed cycle's job."""
    overdue = _source(db_session, last_scheduled_cycle_at=None)  # never scheduled -- simulates a fresh/missed source

    jobs = run_scheduler_tick(db_session)  # the worker's real startup catch-up call, real current time
    assert len(jobs) == 1
    assert jobs[0].payload["source_id"] == str(overdue.id)
    assert overdue.last_scheduled_cycle_at == most_recent_cycle_at(datetime.now(timezone.utc))


def test_restart_after_cycle_already_processed_creates_no_new_jobs(db_session):
    """The idempotency half of the same requirement: if the current
    cycle was ALREADY fully processed before a restart, the restart's
    catch-up tick must be a genuine no-op, not 'a fresh normal scheduler
    run' that re-enqueues everything."""
    current_cycle = most_recent_cycle_at(datetime.now(timezone.utc))
    _source(db_session, last_scheduled_cycle_at=current_cycle)  # already handled this cycle

    jobs = run_scheduler_tick(db_session)  # simulated restart's catch-up tick, real current time
    assert len(jobs) == 0


# --- Observability ---

def test_estimate_next_due_at_for_never_scheduled_source_is_none():
    source = DiscoverySource(frequency="daily", enabled=True, last_scheduled_cycle_at=None)
    assert estimate_next_due_at(source) is None


def test_estimate_next_due_at_lands_exactly_on_the_next_cycle_boundary():
    source = DiscoverySource(frequency="weekly", enabled=True, last_scheduled_cycle_at=MON_0600)
    assert estimate_next_due_at(source) == MON_0600 + timedelta(days=7)


def test_estimate_next_due_at_for_disabled_source_is_none():
    source = DiscoverySource(frequency="daily", enabled=False, last_scheduled_cycle_at=MON_0600)
    assert estimate_next_due_at(source) is None


def test_scheduler_diagnostics_flags_collector_error_distinctly_from_stalled(db_session):
    now = datetime.now(timezone.utc)
    current_cycle = most_recent_cycle_at(now)
    # Stamped for the current cycle, and that run recorded an error --
    # a COLLECTOR problem, correctly picked up by the scheduler.
    _source(db_session, last_scheduled_cycle_at=current_cycle, last_error="upstream 503")
    # Never stamped at all despite presumably being enabled a while --
    # looks like the global SCHEDULER itself did not tick.
    _source(db_session, last_scheduled_cycle_at=None)
    # Wait -- a never-stamped source is also simply "due"/first-run; to
    # distinguish "possibly_stalled" from ordinary first-run due-ness,
    # scheduler_diagnostics treats a due source with no active job as
    # stalled only when it's overdue by stale_multiplier cycles OR never
    # stamped -- so a brand new source correctly still shows this way
    # until the scheduler actually ticks for it, which is the intended
    # signal (nothing has claimed it yet).
    _source(db_session, last_scheduled_cycle_at=current_cycle)  # healthy, current, no error

    diagnostics = scheduler_diagnostics(db_session, now=now)
    statuses = [row["status"] for row in diagnostics["sources"]]
    assert "collector_error" in statuses
    assert "possibly_stalled" in statuses
    assert "ok" in statuses
    assert diagnostics["next_scheduler_cycle_at"] is not None
    assert diagnostics["current_cycle_at"] == current_cycle


def test_scheduler_diagnostics_marks_disabled_source(db_session):
    _source(db_session, enabled=False, last_scheduled_cycle_at=None)
    diagnostics = scheduler_diagnostics(db_session)
    assert diagnostics["sources"][0]["status"] == "disabled"
