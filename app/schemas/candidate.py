import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ContentType,
    NewsletterStatus,
    RagStatus,
    ScientificScope,
    SourceReliability,
)


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    content_type: ContentType
    title: str
    subtitle: str | None = None
    language: str
    discovered_at: datetime
    original_date: date | None = None
    last_source_update: datetime | None = None

    source_id: uuid.UUID | None = None
    source_name: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    source_tier: str | None = None
    authors: list[Any] = Field(default_factory=list)
    institution: str | None = None
    journal: str | None = None
    doi: str | None = None
    pmid: str | None = None
    clinical_trial_id: str | None = None
    publisher: str | None = None
    source_reliability: SourceReliability

    scope: ScientificScope | None = None
    cmt_subtypes: list[str] = Field(default_factory=list)
    genes: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    study_type: str | None = None

    population: str | None = None
    intervention: str | None = None
    comparator: str | None = None
    outcome: str | None = None
    key_findings: str | None = None
    limitations: str | None = None

    has_manual_override: bool
    newsletter_status: NewsletterStatus
    rag_status: RagStatus
    abstract: str | None = None

    created_at: datetime
    updated_at: datetime


class CandidateFilterParams(BaseModel):
    content_type: ContentType | None = None
    source_id: uuid.UUID | None = None
    scope: ScientificScope | None = None
    gene: str | None = None
    subtype: str | None = None
    topic: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    min_cmt_relevance_score: int | None = Field(None, ge=0, le=100)
    newsletter_status: NewsletterStatus | None = None
    rag_status: RagStatus | None = None
    review_status: str | None = None  # convenience alias, see router for mapping
    q: str | None = Field(None, description="Free-text search over title/authors/doi/pmid/ctid/source")


class CandidateOverrideUpdate(BaseModel):
    """
    Fields an administrator can manually override (spec #29). Every field
    set here wins over the AI's proposed classification and is recorded in
    the audit log with old/new values.
    """

    content_type: ContentType | None = None
    scope: ScientificScope | None = None
    cmt_subtypes: list[str] | None = None
    genes: list[str] | None = None
    topics: list[str] | None = None
    study_type: str | None = None
    population: str | None = None
    intervention: str | None = None
    comparator: str | None = None
    outcome: str | None = None
    key_findings: str | None = None
    limitations: str | None = None
    source_reliability: SourceReliability | None = None
    reason: str | None = Field(None, description="Why this override was made (recorded in audit log)")
