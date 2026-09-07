import uuid
from datetime import datetime

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime, text

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class DiscoveryJob(Base, TimestampMixin):
    """
    PostgreSQL-backed job queue (spec #3/#31-34/#49) -- deliberately no
    Redis dependency for v1.

    The worker claims jobs with a `SELECT ... FOR UPDATE SKIP LOCKED`
    query (see app/worker/job_queue.py), which gives safe concurrent
    claiming across multiple worker processes using only Postgres.

    `dedupe_key` gives idempotent enqueueing (spec #49): enqueuing the same
    logical job twice (e.g. two near-simultaneous "run now" clicks for the
    same source) is a no-op if a queued/running job with the same key
    already exists. This is enforced at the DATABASE level by
    `uq_jobs_dedupe_key_active` below -- a partial unique index on
    `dedupe_key` scoped to rows where `status IN ('queued', 'running')` --
    not just by the application's check-then-insert logic in
    `app/worker/job_queue.py::enqueue()`. The application-level check is
    still there as a fast path that avoids an unnecessary failed insert
    in the common (non-racing) case, but the index is what actually
    prevents two concurrent callers from both creating an active
    duplicate job for the same dedupe_key; `enqueue()` catches the
    resulting IntegrityError and returns the winner. Multiple NULL
    `dedupe_key` values, and multiple non-active (completed/failed/
    dead_letter) rows sharing the same key, are unaffected -- standard
    SQL treats NULLs as distinct for uniqueness, and the index only
    applies to the two "active" statuses.
    """

    __tablename__ = "discovery_jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)

    job_type: Mapped[str] = mapped_column(String(50), nullable=False)  # enums.JobType
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")  # enums.JobStatus

    payload: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # earliest claim time (backoff)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(255), nullable=True)  # worker instance id

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Optional checkpoint state a long-running job can persist so that a
    # restart can resume rather than starting over (spec #31).
    checkpoint: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)

    __table_args__ = (
        Index("ix_jobs_status_available", "status", "available_at"),
        Index("ix_jobs_type_status", "job_type", "status"),
        Index("ix_jobs_dedupe_key", "dedupe_key"),
        # Concurrency-safe idempotency (spec #34, #49): the database, not
        # just application logic, prevents two active (queued/running)
        # jobs from ever sharing the same dedupe_key. `sqlite_where` is
        # included so the same constraint is exercised by the SQLite test
        # suite, not only in PostgreSQL production.
        Index(
            "uq_jobs_dedupe_key_active",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )
