import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import CollectionMethod, SourceTier, SourceType


class SourceBase(BaseModel):
    source_name: str
    source_type: SourceType
    source_tier: SourceTier
    base_url: str | None = None
    collection_method: CollectionMethod
    enabled: bool = True
    frequency: str = "daily"
    configuration: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    source_name: str | None = None
    source_tier: SourceTier | None = None
    base_url: str | None = None
    frequency: str | None = None
    configuration: dict[str, Any] | None = None
    notes: str | None = None


class SourceOut(SourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    # FINAL FOCUSED CORRECTION -- SCHEDULER, SOURCE IMPLEMENTATION, TESTS
    # & COMPLETE ZIP, TASK 1: the authoritative field for SCHEDULING
    # due-ness (the fixed 06:00 Asia/Kolkata cycle this source last
    # participated in) -- distinct from `last_run_at` above, which is
    # only a collection-completion timestamp and is never used to decide
    # the next scheduled run. See app/worker/scheduler.py.
    last_scheduled_cycle_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    # "the system can identify... next expected run... current status."
    # Computed from this object's own fields only (no extra DB query), so
    # listing sources stays cheap. This is a lightweight per-source
    # summary; the fuller picture that also cross-references the job
    # queue (distinguishing "collector failed" from "the global
    # scheduler itself may not be ticking") is
    # GET /sources/scheduler/status (app.worker.scheduler.scheduler_diagnostics).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def next_expected_run_at(self) -> datetime | None:
        if not self.enabled:
            return None
        if self.last_scheduled_cycle_at is None:
            return None  # already due -- there is no future "next" time
        from app.worker.scheduler import frequency_window
        from app.worker.ist_scheduler import as_aware_utc, most_recent_cycle_at, next_cycle_at

        last_cycle = as_aware_utc(self.last_scheduled_cycle_at)
        earliest_due_at = last_cycle + frequency_window(self.frequency)
        # Round up to the next actual 06:00 IST cycle boundary at/after
        # that point -- see app.worker.scheduler.estimate_next_due_at,
        # whose cycle-rounding logic this mirrors (kept inline here so
        # this schema doesn't need a full ORM DiscoverySource instance,
        # only the plain fields already on this pydantic model).
        candidate = most_recent_cycle_at(earliest_due_at)
        return candidate if candidate == earliest_due_at else next_cycle_at(earliest_due_at)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.last_error:
            return "error"
        return "ok"


class SourceRunTriggerResponse(BaseModel):
    run_id: uuid.UUID
    job_id: uuid.UUID
    status: str
