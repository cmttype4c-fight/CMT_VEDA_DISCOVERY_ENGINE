import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class DiscoveryRun(Base, TimestampMixin):
    """
    One execution of a source collector (spec #13).

    A failure in one run must never block other sources -- runs are
    independent rows keyed by source_id, and the worker processes each
    source's `collect_source` job independently (see app/worker).
    """

    __tablename__ = "discovery_runs"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    source_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("discovery_sources.id"), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")  # enums.RunStatus

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    records_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # CMT-specific overhaul, Phase 2C/10: NEW records that failed the CMT
    # eligibility gate (retained for provenance, no candidate created).
    cmt_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_details: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)

    # Idempotency / duplicate-run protection (spec #34, #49): a run for a
    # given source in a given "slot" (e.g. calendar day for daily sources)
    # is only created once; a partial unique index enforces this at the DB
    # layer for currently-active runs (see alembic migration).
    run_key: Mapped[str] = mapped_column(String(255), nullable=False)

    source = relationship("DiscoverySource")

    __table_args__ = (
        Index("ix_discovery_runs_source_status", "source_id", "status"),
        UniqueConstraint("source_id", "run_key", name="uq_discovery_runs_source_run_key"),
    )
