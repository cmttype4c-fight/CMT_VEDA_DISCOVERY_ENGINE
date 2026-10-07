"""
Candidate Workspace schema (Final Functional Requirements, spec item 4):
"a consolidated candidate workspace/detail API ... to avoid Veda having
to reconstruct a candidate from many separate API calls."

Every nested block here is read from a table Discovery already owns and
already exposes through its own dedicated endpoint (candidates, analysis,
documents, editorial, newsletter, rag, audit) -- this schema introduces
no new data, just one consolidated read of it. `document` is the
lightweight summary (no `extracted_text`; see app/schemas/document.py);
Veda fetches the actual full text via `GET /candidates/{id}/document`
only when it is about to feed Gemini (spec item 5).
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.audit import AuditLogOut
from app.schemas.candidate import CandidateOut
from app.schemas.document import DiscoveryDocumentSummaryOut
from app.schemas.editorial import EditorialDraftOut
from app.schemas.newsletter import NewsletterItemOut
from app.schemas.rag import RagIngestionRequestOut


class SourceSummaryOut(BaseModel):
    """The originating `DiscoverySource` registry entry (spec item 4:
    "source metadata"). `DiscoveryCandidate` already carries a flat,
    denormalized subset of this (source_name/source_type/source_url/
    source_tier) for fast list/filter queries; this block is the fuller
    source-registry record behind it."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_name: str
    source_type: str
    source_tier: str
    base_url: str | None = None
    collection_method: str
    enabled: bool


class AnalysisSummaryOut(BaseModel):
    """The latest Veda Intelligence `DiscoveryAnalysis` pass for this
    candidate (spec item 4: "CMT relevance/analysis"). None if the
    candidate has not been analysed yet."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
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
    generated_at: datetime


class CandidateWorkspaceOut(BaseModel):
    """
    Consolidated candidate detail (spec item 4). Every field is sourced
    from Discovery's own authoritative tables (spec item 6) -- no RAG
    implementation detail is read or exposed here beyond Discovery's own
    `rag_ingestion_requests` tracking row (`rag`), which Discovery already
    owns and already exposes via `GET /candidates/{id}/rag/status`; this
    is a reference to that same record, not a new RAG capability.
    """

    candidate: CandidateOut
    source: SourceSummaryOut | None = None
    analysis: AnalysisSummaryOut | None = None
    document: DiscoveryDocumentSummaryOut | None = None
    editorial_draft: EditorialDraftOut | None = None
    newsletter: NewsletterItemOut | None = None
    rag: RagIngestionRequestOut | None = None
    recent_audit: list[AuditLogOut] = Field(default_factory=list)
