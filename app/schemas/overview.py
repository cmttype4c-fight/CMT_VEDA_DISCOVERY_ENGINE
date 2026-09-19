"""
CMT Veda compatibility: GET /api/v1/overview response shape.

No contract for this endpoint's shape existed anywhere in the repository
(confirmed by search before writing this). This schema was proposed in
the pre-implementation plan and reflects simple aggregate counts already
derivable from existing columns (content_type, newsletter_status,
rag_status, scope, enabled, status, is_current, is_ai_generated) -- no
new model, no new persisted data. If the actual CMT Veda contract turns
out to need different field names or additional breakdowns, only this
file and the query in app/api/routers/overview.py need to change.
"""
from datetime import datetime

from pydantic import BaseModel, Field


class CandidateCounts(BaseModel):
    total: int
    by_content_type: dict[str, int] = Field(default_factory=dict)
    by_newsletter_status: dict[str, int] = Field(default_factory=dict)
    by_rag_status: dict[str, int] = Field(default_factory=dict)
    by_scope: dict[str, int] = Field(default_factory=dict)


class SourceCounts(BaseModel):
    total: int
    enabled: int
    disabled: int


class RunCounts(BaseModel):
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    last_run_at: datetime | None = None


class EditorialDraftCounts(BaseModel):
    total: int
    current: int
    ai_generated: int


class OverviewOut(BaseModel):
    candidates: CandidateCounts
    sources: SourceCounts
    runs: RunCounts
    editorial_drafts: EditorialDraftCounts
    generated_at: datetime
