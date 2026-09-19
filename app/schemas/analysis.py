import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AnalysisRequest(BaseModel):
    force_reanalysis: bool = Field(False, description="Re-run even if a current analysis already exists")


class AnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID

    cmt_relevance_score: int
    peripheral_neuropathy_relevance_score: int
    clinical_relevance_score: int
    research_importance_score: int
    patient_relevance_score: int

    analysis_confidence: int
    selection_reason: str | None = None

    proposed_scope: str | None = None
    proposed_cmt_subtypes: list[str] = Field(default_factory=list)
    proposed_genes: list[str] = Field(default_factory=list)
    proposed_topics: list[str] = Field(default_factory=list)

    analysis_version: int
    model_name: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    taxonomy_version: str | None = None
    is_latest: bool
    generated_at: datetime
