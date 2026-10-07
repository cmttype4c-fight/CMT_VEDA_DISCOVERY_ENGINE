"""
Original document/full-text schema (Final Functional Requirements, spec
items 2 and 5): this is deliberately the ONLY place `extracted_text`
(the actual acquired scientific source text, verbatim from the
PDF/XML/HTML) is ever returned by this API. It never appears on
`CandidateOut` or in the candidate workspace response (spec item 4) --
kept out of those on purpose so a candidate list/workspace call never
has to transfer a potentially large article body; Veda fetches it only
when it is actually about to feed Gemini (spec item 5's stated purpose),
via `GET /candidates/{id}/document`.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DiscoveryDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID

    doi: str | None = None
    pmid: str | None = None

    source: str
    full_text_source: str | None = None
    document_url: str | None = None

    retrieved_at: datetime | None = None
    mime_type: str | None = None
    # "pdf" | "xml" | "html" | "other" | None -- the ACTUAL format
    # obtained, never a claimed-but-unverified one (spec item 2:
    # "abstract-only material must not be represented as full text" --
    # the same principle this field has always enforced for format).
    full_text_format: str | None = None
    pdf_available: bool = False
    file_size: int | None = None
    content_hash: str | None = None
    document_ref: str | None = None
    license_provenance: str | None = None

    # queued | resolving | acquired | unavailable | unsupported | failed
    retrieval_status: str
    error_detail: str | None = None

    # Populated ONLY when retrieval_status == "acquired" AND
    # extraction_status == "success" (app/services/fulltext/service.py
    # never sets it otherwise) -- this is the field Veda feeds to Gemini
    # for Newsletter drafting (spec item 5). None here means "no
    # original full text available for this candidate"; Veda must fall
    # back to the candidate's `abstract` and MUST NOT present that
    # abstract as full text (spec item 2).
    extracted_text: str | None = None
    extracted_char_count: int | None = None
    extraction_status: str  # not_attempted | success | failed
    extraction_error: str | None = None

    created_at: datetime


class DiscoveryDocumentSummaryOut(BaseModel):
    """
    Lightweight document block embedded in `GET /candidates/{id}/workspace`
    (spec item 4: "original full-text availability ... document
    information ... source provenance"). Deliberately omits
    `extracted_text` -- the workspace response is meant to be cheap
    enough to load for any candidate list row; the full article body is
    only ever served by the dedicated `GET /candidates/{id}/document`
    (spec item 5), so a client that only needs to know "is full text
    available, and from where" never pays for transferring it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_text_source: str | None = None
    document_url: str | None = None
    full_text_format: str | None = None
    pdf_available: bool = False
    retrieval_status: str
    error_detail: str | None = None
    extraction_status: str
    extracted_char_count: int | None = None
    license_provenance: str | None = None
    retrieved_at: datetime | None = None
