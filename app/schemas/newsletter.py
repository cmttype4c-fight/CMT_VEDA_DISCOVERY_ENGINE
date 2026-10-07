import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import NewsletterStatus


class NewsletterItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    editorial_draft_id: uuid.UUID | None = None
    status: NewsletterStatus

    selected_by: str | None = None
    selected_at: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_by: str | None = None
    rejected_at: datetime | None = None
    rejection_reason: str | None = None
    scheduled_for: datetime | None = None
    # CMT Veda final-contract pass (spec items 9/10).
    section: str | None = None
    published_at: datetime | None = None


class NewsletterSectionUpdate(BaseModel):
    """Body for `PATCH /candidates/{id}/newsletter/section` (spec item 9)."""

    section: str = Field(..., min_length=1, max_length=100)


class NewsletterRejectRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class NewsletterScheduleRequest(BaseModel):
    scheduled_for: datetime
    publication_id: uuid.UUID | None = Field(
        None, description="Existing publication to attach this item to; a new one is created if omitted"
    )


class NewsletterPublicationCreate(BaseModel):
    title: str
    scheduled_for: datetime | None = None


class NewsletterPublicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: str
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    item_ids: list[str]
    created_by: str | None = None
    published_by: str | None = None


# --- CMT Veda compatibility schemas (app/api/routers/newsletter.py) ---
# Added for the editorial-status dispatcher and bulk endpoint. Both are
# thin adapters over the existing select/review/approve/reject/archive
# functions above -- these schemas carry no new business fields, just
# the target status (and, for bulk, which candidates).


class EditorialStatusTransitionRequest(BaseModel):
    """Target status for POST .../editorial-status. Value must be one of
    the existing NewsletterStatus values this dispatcher supports:
    selected | under_review | approved | rejected | archived. `reason`
    is required when status == 'rejected' (mirrors NewsletterRejectRequest)."""

    status: str
    reason: str | None = None


class BulkCandidateAction(BaseModel):
    candidate_ids: list[uuid.UUID] = Field(..., min_length=1)
    status: str
    reason: str | None = None


class BulkResultItem(BaseModel):
    candidate_id: uuid.UUID
    success: bool
    error: str | None = None


class BulkResult(BaseModel):
    results: list[BulkResultItem]
    succeeded: int
    failed: int


# --- Published Newsletter feed (spec item 10, GET /newsletter/published) ---


class PublishedNewsletterItemOut(BaseModel):
    """
    One entry in the public Newsletter feed. Deliberately flattens
    candidate + current-editorial-draft fields into a single row so Veda
    can render the public Newsletter straight from this list, with no
    follow-up per-candidate request (spec item 10 -- "without an N+1
    candidate-detail request pattern"). See
    app/api/routers/newsletter.py::list_published for how this is built
    (candidate + NewsletterItem in one query, current drafts fetched in
    one bulk second query -- two queries total regardless of page size).

    Scientific-source fields (title/authors/journal/doi/pmid/source_url)
    come from `DiscoveryCandidate`; `headline`/`summary`/`why_it_matters`/
    `key_points` come from the candidate's current `DiscoveryEditorialDraft`
    and are clearly AI/editorial content, not the original source (spec
    item 2's "clearly distinguish" requirement, carried through here too)
    -- `is_ai_generated` says which of the two a given draft actually is.
    """

    model_config = ConfigDict(from_attributes=True)

    candidate_id: uuid.UUID
    newsletter_item_id: uuid.UUID
    section: str | None = None
    published_at: datetime | None = None
    scheduled_for: datetime | None = None

    title: str
    authors: list = Field(default_factory=list)
    journal: str | None = None
    source_name: str | None = None
    source_url: str | None = None
    doi: str | None = None
    pmid: str | None = None
    content_type: str
    full_text_available: bool = False

    headline: str | None = None
    summary: str | None = None
    why_it_matters: str | None = None
    key_points: list[str] = Field(default_factory=list)
    is_ai_generated: bool | None = None
