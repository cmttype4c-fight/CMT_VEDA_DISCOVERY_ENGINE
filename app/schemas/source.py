import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CollectionMethod, SourceTier, SourceType


class SourceBase(BaseModel):
    source_name: str
    source_type: SourceType
    source_tier: SourceTier
    base_url: str | None = None
    collection_method: CollectionMethod
    enabled: bool = True
    frequency: str = "daily"
    configuration: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    source_name: str | None = None
    source_tier: SourceTier | None = None
    base_url: str | None = None
    frequency: str | None = None
    configuration: dict[str, Any] | None = None
    notes: str | None = None


class SourceOut(SourceBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class SourceRunTriggerResponse(BaseModel):
    run_id: uuid.UUID
    job_id: uuid.UUID
    status: str
