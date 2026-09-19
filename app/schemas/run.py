import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models.enums import RunStatus


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    status: RunStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    records_found: int
    new_records: int
    duplicates: int
    updated_records: int
    candidates_created: int
    cmt_rejected: int = 0
    errors: int
    error_details: list[Any]
    created_at: datetime
