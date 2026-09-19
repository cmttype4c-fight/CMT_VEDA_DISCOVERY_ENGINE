import uuid
from datetime import datetime

from sqlalchemy import Boolean, Date, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class DiscoveryCandidate(Base, TimestampMixin):
    """
    The Discovery Candidate: the central business object of the engine
    (spec #14). Not limited to research papers -- see ContentType.

    Modeled as three logical layers per spec:
      Layer 1 - Identity
      Layer 2 - Source & Evidence
      Layer 3 - Scientific classification

    `newsletter_status` / `rag_status` are denormalized read-optimized
    copies of the authoritative state that lives in `newsletter_items` /
    `rag_ingestion_requests` (spec #22/#23 state machines). They exist only
    to make candidate list/filter APIs fast (spec #38) and are written
    exclusively by the workflow services -- never mutated directly by a
    generic PATCH (spec #50).
    """

    __tablename__ = "discovery_candidates"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)

    # ---- Layer 1: Identity ----
    content_type: Mapped[str] = mapped_column(String(50), nullable=False)  # enums.ContentType
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    original_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    last_source_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---- Layer 2: Source & Evidence ----
    source_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("discovery_sources.id"), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    source_tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    authors: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    institution: Mapped[str | None] = mapped_column(String(512), nullable=True)
    journal: Mapped[str | None] = mapped_column(String(512), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(50), nullable=True)
    clinical_trial_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_reliability: Mapped[str] = mapped_column(String(20), nullable=False, default="unknown")

    # ---- Layer 3: Scientific classification ----
    scope: Mapped[str | None] = mapped_column(String(50), nullable=True)  # enums.ScientificScope
    cmt_subtypes: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    genes: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    topics: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    study_type: Mapped[str | None] = mapped_column(String(100), nullable=True)

    population: Mapped[str | None] = mapped_column(Text, nullable=True)
    intervention: Mapped[str | None] = mapped_column(Text, nullable=True)
    comparator: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Was any scientific-classification field manually overridden by an
    # administrator? (spec #29) Individual overrides are captured in the
    # audit log; this flag makes it cheap to filter/display in the UI.
    has_manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ---- Denormalized workflow status (see class docstring) ----
    newsletter_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_selected")
    rag_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_selected")

    # Abstract/description carried through for convenience/search; the
    # authoritative copy lives on the originating source record(s).
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)

    # CMT-specific overhaul, Phase 5/6: denormalized read-optimized flag,
    # same pattern as newsletter_status/rag_status above -- the
    # authoritative record is discovery_documents (app/models/document.py).
    # A missing/failed full-text acquisition never invalidates the
    # candidate; this stays false and the candidate is otherwise unaffected.
    full_text_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_candidates_content_type", "content_type"),
        Index("ix_candidates_scope", "scope"),
        Index("ix_candidates_discovered_at", "discovered_at"),
        Index("ix_candidates_newsletter_status", "newsletter_status"),
        Index("ix_candidates_rag_status", "rag_status"),
        Index("ix_candidates_doi", "doi"),
        Index("ix_candidates_pmid", "pmid"),
        Index("ix_candidates_ctid", "clinical_trial_id"),
    )
