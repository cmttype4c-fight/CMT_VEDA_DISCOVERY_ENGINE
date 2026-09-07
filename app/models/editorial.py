import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid, utcnow


class DiscoveryEditorialDraft(Base, TimestampMixin):
    """
    A structured editorial draft generated (or hand-written) for a
    candidate (spec #20-21).

    Versioned: editing a draft never modifies the original source record
    (that invariant lives in the service layer, app/services/intelligence/
    editorial_service.py) and never overwrites a prior draft version --
    a new row is inserted with `draft_version` incremented and
    `is_current` flipped.
    """

    __tablename__ = "discovery_editorial_drafts"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("discovery_candidates.id"), nullable=False)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("discovery_analysis.id"), nullable=True)

    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    key_points: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    detailed_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    cmt_relevance_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitations: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    disclaimer: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=(
            "This content is for informational purposes only, is generated with "
            "AI assistance from published sources, and is not medical advice, "
            "diagnosis, or a treatment recommendation."
        ),
    )

    draft_status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")  # enums.DraftStatus
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_edited_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # True if this row was hand-authored/edited rather than purely
    # AI-generated (spec #21: AI content must be distinguishable).
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    generation_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("ix_editorial_candidate", "candidate_id"),
        Index("ix_editorial_candidate_current", "candidate_id", "is_current"),
    )
