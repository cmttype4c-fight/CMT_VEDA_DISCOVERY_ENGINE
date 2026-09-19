"""
Wall-clock cycle computation for the global Discovery scheduler (FINAL
CORRECTION -- SOURCE EXPANSION + 6:00 AM IST SCHEDULER, TASK 4/5/6).

This module is deliberately pure stdlib (`datetime` + `zoneinfo`, both in
the standard library since Python 3.9) and has ZERO dependency on
SQLAlchemy, FastAPI, or any other piece of this codebase. That is not
just tidiness: it means these functions can be (and were) executed and
verified directly in the build sandbox, unlike almost everything else in
this repository, which remains blocked by this sandbox's proxy-level
PyPI restriction (see IMPLEMENTATION_STATUS.md). See
tests/test_ist_scheduler.py for the executed test cases.

The requirement ("every day at 06:00 AM, timezone Asia/Kolkata, do not
rely on the VPS's system timezone") is satisfied by always resolving the
wall-clock hour/minute through an explicit `ZoneInfo("Asia/Kolkata")`
object rather than anything derived from the host OS's local timezone
setting (`time.localtime`, naive `datetime.now()`, etc. are never used
here). All public functions accept and return timezone-AWARE UTC
datetimes at their boundary, so callers (the DB layer, `last_run_at`
comparisons) never have to reason about IST directly -- only this module
does, in one place.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Requirement: "Every day at 06:00 AM, Timezone: Asia/Kolkata."
DEFAULT_HOUR = 6
DEFAULT_MINUTE = 0
DEFAULT_TZ_NAME = "Asia/Kolkata"


def _require_aware(moment: datetime, *, param_name: str = "moment") -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(
            f"{param_name} must be a timezone-aware datetime (got a naive "
            f"datetime) -- this module never assumes a local/system "
            f"timezone, by design."
        )
    return moment


def next_cycle_at(
    after: datetime,
    *,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    tz_name: str = DEFAULT_TZ_NAME,
) -> datetime:
    """
    The next occurrence of `hour:minute` in the `tz_name` timezone that is
    strictly AFTER `after`, returned as a timezone-aware UTC datetime.

    `after` must itself be timezone-aware (see `_require_aware`) -- this
    is intentional friction against accidentally passing a naive
    `datetime.now()` (which would silently be interpreted in whatever
    timezone Python assumes, defeating the entire point of this module).

    Correct across DST-observing timezones in general (not that
    Asia/Kolkata observes DST -- it has used a fixed UTC+05:30 offset
    since 1945 -- but the implementation does not hard-code that offset;
    it always asks `zoneinfo` for the correct one, so it stays correct
    if this is ever pointed at a different, DST-observing timezone).
    """
    _require_aware(after, param_name="after")
    tz = ZoneInfo(tz_name)
    local_after = after.astimezone(tz)

    candidate = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local_after:
        candidate = candidate + timedelta(days=1)

    return candidate.astimezone(timezone.utc)


def seconds_until_next_cycle(
    after: datetime,
    *,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    tz_name: str = DEFAULT_TZ_NAME,
) -> float:
    """Convenience wrapper used by the recurring sleep loop."""
    target = next_cycle_at(after, hour=hour, minute=minute, tz_name=tz_name)
    return max(0.0, (target - after).total_seconds())


def as_aware_utc(moment: datetime | None) -> datetime | None:
    """
    Normalize a datetime that may have come back from the database as
    timezone-NAIVE (this codebase's own SQLite test backend does this
    even though every column is declared `DateTime(timezone=True)` --
    SQLite has no native timezone-aware storage, so the value round-trips
    without its tzinfo) into an aware UTC datetime, so it can safely be
    compared against/subtracted from another aware datetime without
    raising `TypeError: can't subtract offset-naive and offset-aware
    datetimes`.

    This assumes -- correctly, for every timestamp this codebase writes
    -- that a naive value already represents UTC (every write path uses
    `datetime.now(timezone.utc)` / `app.models.base.utcnow()`), so this
    is purely a "restore the tzinfo that should have been there" fix, not
    a timezone *conversion*. An already-aware datetime is converted to
    UTC (a no-op if it's already UTC) for consistency. `None` passes
    through unchanged, since "no timestamp yet" is a meaningful, distinct
    value from any particular time (used throughout
    app/worker/scheduler.py for "this source has never run").

    Deliberately separate from `_require_aware` (used by `next_cycle_at`/
    `most_recent_cycle_at` above): THIS function is only safe to use on a
    value we know originated from this codebase's own UTC-only write
    path (a database column). It must never be used on a raw
    `datetime.now()` from an unknown caller, since a genuinely-naive
    "current time" is ambiguous (could be local time) rather than known
    to be UTC -- that case should keep raising, not be silently coerced.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def most_recent_cycle_at(
    at_or_before: datetime,
    *,
    hour: int = DEFAULT_HOUR,
    minute: int = DEFAULT_MINUTE,
    tz_name: str = DEFAULT_TZ_NAME,
) -> datetime:
    """
    The most recent occurrence of `hour:minute` in `tz_name` that is at or
    before `at_or_before` -- the "intended cycle timestamp" a tick firing
    around now should be treated as having fired at. Used so that
    `due_sources()` compares against a fixed, non-drifting cycle boundary
    rather than the (slightly later, and inconsistent run-to-run) actual
    wall-clock completion time -- see app/worker/scheduler.py.
    """
    _require_aware(at_or_before, param_name="at_or_before")
    tz = ZoneInfo(tz_name)
    local_now = at_or_before.astimezone(tz)

    candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > local_now:
        candidate = candidate - timedelta(days=1)

    return candidate.astimezone(timezone.utc)
