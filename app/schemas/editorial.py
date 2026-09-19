import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EditorialGenerateRequest(BaseModel):
    force_regenerate: bool = False


class EditorialDraftUpdate(BaseModel):
    """Human edits to a draft. Never touches the source record (spec #20)."""

    headline: str | None = None
    summary: str | None = None
    why_it_matters: str | None = None
    key_points: list[str] | None = None
    detailed_content: str | None = None
    cmt_relevance_explanation: str | None = None
    limitations: str | None = None
    references: list[dict[str, Any]] | None = None
    draft_status: str | None = Field(None, description="draft | in_review | approved")


class EditorialDraftOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    analysis_id: uuid.UUID | None = None

    headline: str
    summary: str
    why_it_matters: str | None = None
    key_points: list[str] = Field(default_factory=list)
    detailed_content: str | None = None
    cmt_relevance_explanation: str | None = None
    limitations: str | None = None
    references: list[Any] = Field(default_factory=list)
    disclaimer: str

    draft_status: str
    draft_version: int
    is_current: bool

    generated_at: datetime
    last_edited_at: datetime | None = None
    last_edited_by: str | None = None
    is_ai_generated: bool
    generation_model: str | None = None
