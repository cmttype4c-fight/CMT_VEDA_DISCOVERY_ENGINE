"""
FINAL CORRECTION -- SOURCE EXPANSION + 6:00 AM IST SCHEDULER, TASK 10
(scheduler tests): timezone correctness for the global 06:00 Asia/Kolkata
scheduler cycle.

Pure unit tests for app/worker/ist_scheduler.py -- stdlib only
(datetime + zoneinfo), no DB, no SQLAlchemy, no network. These were
executed directly in the build sandbox (see IMPLEMENTATION_STATUS.md);
this file mirrors that hand-run verification as real pytest cases.
"""
import os
import time
from datetime import datetime, timezone

import pytest

from app.worker.ist_scheduler import (
    as_aware_utc,
    most_recent_cycle_at,
    next_cycle_at,
    seconds_until_next_cycle,
)


def test_next_cycle_same_day_before_0600_ist():
    # 00:00 UTC == 05:30 IST -- the 06:00 IST cycle is still ahead today.
    after = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    assert next_cycle_at(after) == datetime(2026, 1, 15, 0, 30, 0, tzinfo=timezone.utc)


def test_next_cycle_rolls_to_next_day_once_past_0600_ist():
    # 00:31 UTC == 06:01 IST -- today's cycle already passed.
    after = datetime(2026, 1, 15, 0, 31, 0, tzinfo=timezone.utc)
    assert next_cycle_at(after) == datetime(2026, 1, 16, 0, 30, 0, tzinfo=timezone.utc)


def test_next_cycle_is_strictly_after_not_inclusive():
    """A tick firing exactly at the boundary must schedule TOMORROW's
    cycle next, never fire twice for the same moment."""
    at_boundary = datetime(2026, 1, 15, 0, 30, 0, tzinfo=timezone.utc)
    assert next_cycle_at(at_boundary) == datetime(2026, 1, 16, 0, 30, 0, tzinfo=timezone.utc)


def test_naive_datetime_is_rejected():
    """Never silently falls back to interpreting a naive datetime in the
    system's local timezone -- that is exactly the bug this module exists
    to prevent ('do not rely on the VPS's system timezone')."""
    with pytest.raises(ValueError):
        next_cycle_at(datetime(2026, 1, 15, 0, 0, 0))


def test_result_is_always_utc_aware():
    result = next_cycle_at(datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc))
    assert result.tzinfo == timezone.utc


def test_most_recent_cycle_same_day():
    after = datetime(2026, 1, 15, 0, 35, 0, tzinfo=timezone.utc)  # 06:05 IST
    assert most_recent_cycle_at(after) == datetime(2026, 1, 15, 0, 30, 0, tzinfo=timezone.utc)


def test_most_recent_cycle_rolls_back_when_before_todays_cycle():
    after = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)  # 05:30 IST, before today's 06:00
    assert most_recent_cycle_at(after) == datetime(2026, 1, 14, 0, 30, 0, tzinfo=timezone.utc)


def test_most_recent_cycle_is_inclusive_at_exact_boundary():
    at_boundary = datetime(2026, 1, 15, 0, 30, 0, tzinfo=timezone.utc)
    assert most_recent_cycle_at(at_boundary) == at_boundary


def test_seconds_until_next_cycle():
    after = datetime(2026, 1, 14, 23, 30, 0, tzinfo=timezone.utc)  # 05:00 IST
    assert seconds_until_next_cycle(after) == 3600.0


def test_unaffected_by_process_local_timezone():
    """The core requirement: 'Do not rely on the VPS's system timezone.'
    Changing the process's TZ environment variable must not change the
    result -- IST is always resolved explicitly via zoneinfo, never via
    the host's local time."""
    after = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
    baseline = next_cycle_at(after)

    original_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "America/New_York"
        time.tzset()
        assert next_cycle_at(after) == baseline
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


def test_as_aware_utc_attaches_utc_to_a_naive_datetime():
    """FINAL FOCUSED CORRECTION TASK 4: 'normalize database timestamps
    safely when they come back as timezone-naive values.' This is the
    exact scenario SQLite (this repo's test backend) produces despite
    every column being declared `DateTime(timezone=True)`."""
    naive = datetime(2026, 1, 15, 0, 30, 0)
    result = as_aware_utc(naive)
    assert result.tzinfo == timezone.utc
    assert result.replace(tzinfo=None) == naive


def test_as_aware_utc_passes_through_an_already_aware_utc_value():
    aware = datetime(2026, 1, 15, 0, 30, 0, tzinfo=timezone.utc)
    assert as_aware_utc(aware) == aware


def test_as_aware_utc_converts_non_utc_aware_value_to_utc():
    from datetime import timedelta

    ist = timezone(timedelta(hours=5, minutes=30))
    aware_ist = datetime(2026, 1, 15, 6, 0, 0, tzinfo=ist)  # == 00:30 UTC
    assert as_aware_utc(aware_ist) == datetime(2026, 1, 15, 0, 30, 0, tzinfo=timezone.utc)


def test_as_aware_utc_none_passes_through():
    assert as_aware_utc(None) is None


def test_as_aware_utc_prevents_the_exact_typeerror_scenario():
    """The concrete failure this exists to prevent: subtracting a
    DB-returned naive datetime from an aware 'now' must not raise
    `TypeError: can't subtract offset-naive and offset-aware datetimes`."""
    from datetime import timedelta

    naive_from_db = datetime(2026, 1, 15, 0, 30, 0)
    now = datetime(2026, 1, 16, 0, 30, 0, tzinfo=timezone.utc)
    elapsed = now - as_aware_utc(naive_from_db)  # must not raise
    assert elapsed == timedelta(days=1)


def test_stable_offset_year_round():
    """Asia/Kolkata has used a fixed UTC+05:30 offset since 1945 (no DST) --
    sanity-check the cycle lands on the same UTC offset in January and July."""
    jan = next_cycle_at(datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc))
    jul = next_cycle_at(datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc))
    assert jan.minute == jul.minute == 30
    assert jan.hour == jul.hour == 0
