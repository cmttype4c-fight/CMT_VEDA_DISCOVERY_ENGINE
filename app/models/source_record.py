import uuid
from datetime import datetime

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class DiscoverySourceRecord(Base, TimestampMixin):
    """
    Normalized record produced by a collector (spec #10).

    Every collector -- PubMed, ClinicalTrials.gov, RSS feeds, manual
    submission -- must produce this same shape. Fields the source did not
    provide are left null; the engine never guesses (spec #10).

    `is_current` supports source history (spec #12): when a record changes
    (e.g. a trial's status), we do not silently overwrite -- a new record
    version is inserted and the previous one is marked not-current, while
    `content_hash` lets deduplication detect "nothing actually changed".
    """

    __tablename__ = "discovery_source_records"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)

    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("discovery_sources.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)

    run_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("discovery_runs.id"), nullable=True)

    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    institution: Mapped[str | None] = mapped_column(String(512), nullable=True)
    journal: Mapped[str | None] = mapped_column(String(512), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(512), nullable=True)

    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(50), nullable=True)
    clinical_trial_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    publication_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_metadata: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Set by the deduplication service (spec #11): NEW / DUPLICATE / UPDATED
    dedupe_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Points at the record this one supersedes, when dedupe_status == UPDATED
    supersedes_record_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    candidate_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("discovery_candidates.id"), nullable=True)

    # CMT-specific overhaul, Phase 2C: set by
    # app.services.intelligence.eligibility.assess_eligibility() at
    # collection time, before candidate creation. A NEW record that fails
    # the gate keeps candidate_id NULL (no candidate is created) but is
    # still retained here for provenance/deduplication, per explicit
    # instruction not to let rejected records pollute the candidate queue
    # while still preserving source history.
    cmt_eligible: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    eligibility_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    source = relationship("DiscoverySource")

    __table_args__ = (
        Index("ix_source_records_source_external", "source_id", "external_id"),
        Index("ix_source_records_doi", "doi"),
        Index("ix_source_records_pmid", "pmid"),
        Index("ix_source_records_ctid", "clinical_trial_id"),
        Index("ix_source_records_url", "canonical_url"),
        Index("ix_source_records_current", "is_current"),
    )
