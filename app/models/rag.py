import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class RagIngestionRequest(Base, TimestampMixin):
    """
    Backend RAG workflow / adapter request (spec #23-26).

    IMPORTANT: this does NOT build or own the RAG itself. It is the
    Discovery-side record of "we asked the existing CMT Veda RAG to ingest
    this candidate", tracked through verification, approval, and adapter
    submission. The actual ingestion call goes through
    app/services/rag_adapter.py's RAGIngestionAdapter interface.
    """

    __tablename__ = "rag_ingestion_requests"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("discovery_candidates.id"), nullable=False, unique=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_selected")  # enums.RagStatus

    # Mandatory verification checklist (spec #24) -- ALL must be true
    # before an admin can approve.
    source_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    original_source_accessible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scientific_relevance_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    suitable_for_ask_veda: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_permitted_for_ingestion: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    verified_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rejected_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata payload sent to the RAG adapter (spec #26), snapshotted at
    # submission time so later candidate edits don't retroactively change
    # what was actually sent.
    ingestion_metadata: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)
    knowledge_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    external_ingestion_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_rag_requests_status", "status"),
    )

    @property
    def all_checks_passed(self) -> bool:
        return all(
            [
                self.source_verified,
                self.original_source_accessible,
                self.scientific_relevance_confirmed,
                self.suitable_for_ask_veda,
                self.content_permitted_for_ingestion,
            ]
        )
