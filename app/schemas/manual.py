import uuid

from pydantic import BaseModel, Field

from app.models.enums import ContentType


class ManualDiscoveryRequest(BaseModel):
    """
    Manual submission (spec #27). Goes through the exact same pipeline as
    any collector: Normalize -> Deduplicate -> Candidate -> Analyse ->
    Editorial/RAG. There is no separate manual processing path.
    """

    url: str
    title: str
    content_type: ContentType
    source_name: str = Field(..., description="Free-text label, e.g. 'Manually submitted' or an org name")
    notes: str | None = None


class ManualDiscoveryResponse(BaseModel):
    source_record_id: uuid.UUID
    candidate_id: uuid.UUID | None
    dedupe_status: str
