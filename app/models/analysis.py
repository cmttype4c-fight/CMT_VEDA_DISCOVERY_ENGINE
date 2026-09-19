import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid, utcnow


class DiscoveryAnalysis(Base, TimestampMixin):
    """
    Output of the Veda Intelligence layer for one candidate (spec #17-19).

    Analysis is versioned (spec #30): re-analysing a candidate (new model,
    new prompt, taxonomy update) inserts a new row rather than overwriting;
    `is_latest` marks the current one. Scores are independent 0-100 values;
    confidence is tracked separately from relevance per spec #18.

    The engine treats these scores as *signals*, not scientific truth
    (spec #17) -- they inform editorial/newsletter/RAG workflows, which
    still require human sign-off (spec #22, #24).
    """

    __tablename__ = "discovery_analysis"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("discovery_candidates.id"), nullable=False)

    cmt_relevance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    peripheral_neuropathy_relevance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    clinical_relevance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    research_importance_score: Mapped[int] = mapped_column(Integer, nullable=False)
    patient_relevance_score: Mapped[int] = mapped_column(Integer, nullable=False)

    analysis_confidence: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-100
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Deterministic-rules stage output (spec #17 pipeline: rules -> AI ->
    # structured result) kept alongside the AI stage for traceability/audit.
    rules_output: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)
    ai_output: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)

    # Proposed classification (candidate's scope/genes/subtypes/topics are
    # only updated from this after acceptance; an admin override wins over
    # both, per spec #29).
    proposed_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
    proposed_cmt_subtypes: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    proposed_genes: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    proposed_topics: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)

    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    taxonomy_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    is_latest: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_analysis_candidate", "candidate_id"),
        Index("ix_analysis_candidate_latest", "candidate_id", "is_latest"),
    )
