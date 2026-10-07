"""
Original document/full-text API (Final Functional Requirements, spec
items 2, 5, 6, 7).

Read-only. Serves exactly what `app/services/fulltext/service.py`'s
existing, unchanged acquisition pipeline already produced -- this router
adds no new acquisition logic, and Discovery remains the sole,
authoritative owner of this data (spec item 6: "do not create a second
scientific-document repository in Veda-v1"). Distinct from
`app/api/routers/editorial.py` (the AI-generated Newsletter article) and
from `app/api/routers/rag.py` (the separate RAG workflow, untouched by
this task) -- this is purely "give Veda the original scientific
source/full text it discovered."
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import Principal, require_any_authenticated
from app.models.candidate import DiscoveryCandidate
from app.models.document import DiscoveryDocument
from app.schemas.document import DiscoveryDocumentOut
from app.services.document_service import get_representative_document

router = APIRouter(tags=["documents"])


@router.get("/candidates/{candidate_id}/document", response_model=DiscoveryDocumentOut)
def get_candidate_document(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    """
    The original document/full-text record for this candidate (spec
    item 5), if full-text resolution has ever been attempted for it.

    404 means resolution was never attempted at all -- distinct from a
    200 with `retrieval_status` of `unavailable`/`failed`/`unsupported`,
    which means the engine genuinely tried and the fields above explain
    why (`error_detail`). Either way, per spec item 2, the candidate may
    still be used by Veda with its `abstract` (via `GET /candidates/{id}`)
    -- just never presented as full text.
    """
    doc = get_representative_document(db, candidate.id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No full-text acquisition has been attempted yet for candidate {candidate.id}",
        )
    return doc


@router.get("/documents/{document_id}", response_model=DiscoveryDocumentOut)
def get_document(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    """Direct lookup by the document identifier spec item 5 asks for
    (e.g. as returned in the candidate workspace's `document` block)."""
    doc = db.get(DiscoveryDocument, document_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Document {document_id} not found")
    return doc
