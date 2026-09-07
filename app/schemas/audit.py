import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID | None = None
    action: str
    performed_by: str
    performed_at: datetime
    old_value: dict[str, Any] | None = None
    new_value: dict[str, Any] | None = None
    notes: str | None = None
