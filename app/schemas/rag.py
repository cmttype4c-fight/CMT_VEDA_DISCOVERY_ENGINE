import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import RagStatus


class RagSubmitRequest(BaseModel):
    """Selects a candidate for the RAG path (not_selected -> pending_approval)."""

    notes: str | None = None


class RagVerifyRequest(BaseModel):
    source_verified: bool
    original_source_accessible: bool
    scientific_relevance_confirmed: bool
    suitable_for_ask_veda: bool
    content_permitted_for_ingestion: bool
    notes: str | None = None


class RagRejectRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class RagIngestionRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    status: RagStatus

    source_verified: bool
    original_source_accessible: bool
    scientific_relevance_confirmed: bool
    suitable_for_ask_veda: bool
    content_permitted_for_ingestion: bool
    verification_notes: str | None = None
    verified_by: str | None = None
    verified_at: datetime | None = None

    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None

    external_ingestion_id: str | None = None
    attempt_count: int
    last_error: str | None = None

    queued_at: datetime | None = None
    processing_at: datetime | None = None
    indexed_at: datetime | None = None
    failed_at: datetime | None = None
    removed_at: datetime | None = None
