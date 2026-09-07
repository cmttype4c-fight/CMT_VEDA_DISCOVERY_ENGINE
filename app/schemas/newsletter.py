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
