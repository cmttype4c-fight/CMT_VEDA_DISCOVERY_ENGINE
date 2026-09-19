"""
RAG workflow API (spec #23-26, #36).

`approve` is admin-only and re-validated against all five checklist items
server-side (spec #24, #40 -- never trust the frontend to have hidden the
button). `submit`/`verify`/`reject` are reviewer-or-admin so a reviewer
can do the verification legwork, but only an admin can grant final
approval.

*** ARCHITECTURAL NOTE -- read before ever swapping in a real RAG_ADAPTER ***
`approve` and `retry` below call `adapter.submit()` and `await` its
result INLINE, within the HTTP request/response cycle, carrying the
request all the way from `approved` through `queued` -> `processing` ->
`indexed`/`failed` in one API call. This is only appropriate because the
default adapter (`MockRAGIngestionAdapter`) resolves instantly and
in-memory (spec #47's offline-demonstration requirement). It is
DELIBERATELY NOT how a production integration against a real,
potentially slow or unreliable RAG service should work: a real adapter
call blocking an HTTP request thread has no request timeout margin, no
retry-with-backoff, and ties up a web worker for the duration of an
external call outside this engine's control. The `queued`/`processing`
states in the RagStatus state machine exist precisely because the
original design intent was for a real ingestion call to be driven
asynchronously by the background worker (the same way `collect_source`
and `analyse_candidate` already are), not answered inline by the API.
Moving `approve`/`retry` to enqueue a worker job instead of awaiting the
adapter inline would be the correct production fix, but is a genuine
architecture change and is OUT OF SCOPE for this pass -- see
IMPLEMENTATION_STATUS.md. Do not treat the current synchronous behavior
as validated production design merely because the mock adapter makes it
appear to work end-to-end.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import Principal, require_admin, require_reviewer_or_admin
from app.models.candidate import DiscoveryCandidate
from app.schemas.rag import RagIngestionRequestOut, RagRejectRequest, RagSubmitRequest, RagVerifyRequest
from app.services.rag_adapter import get_rag_adapter
from app.services.workflows import rag_workflow as wf
from app.services.workflows.rag_workflow import InvalidTransition, VerificationIncomplete

router = APIRouter(prefix="/candidates/{candidate_id}/rag", tags=["rag"])


def _handle(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except InvalidTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except VerificationIncomplete as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.post("/submit", response_model=RagIngestionRequestOut)
def submit(
    payload: RagSubmitRequest = RagSubmitRequest(),
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    req = _handle(wf.submit, db, candidate, principal.subject, payload.notes)
    db.commit()
    db.refresh(req)
    return req


@router.post("/verify", response_model=RagIngestionRequestOut)
def verify(
    payload: RagVerifyRequest,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    req = _handle(
        wf.verify,
        db,
        candidate,
        principal.subject,
        source_verified=payload.source_verified,
        original_source_accessible=payload.original_source_accessible,
        scientific_relevance_confirmed=payload.scientific_relevance_confirmed,
        suitable_for_ask_veda=payload.suitable_for_ask_veda,
        content_permitted_for_ingestion=payload.content_permitted_for_ingestion,
        notes=payload.notes,
    )
    db.commit()
    db.refresh(req)
    return req


@router.post("/approve", response_model=RagIngestionRequestOut)
async def approve(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    """Approves AND immediately hands off to the RAG adapter (spec #25-26),
    moving approved -> queued -> processing -> indexed/failed in one call.

    PROVISIONAL v1 SIMPLIFICATION -- see the module docstring above: this
    inline, synchronous hand-off is only safe because the default adapter
    (mock) resolves instantly. It is NOT the final production integration
    design; a real adapter should be driven asynchronously by the worker
    instead of awaited inline here."""
    req = _handle(wf.approve, db, candidate, principal.subject)
    db.commit()

    metadata = _build_rag_metadata(candidate, principal.subject, db)
    req = wf.enqueue(db, candidate, req, metadata, knowledge_version="v1")
    req = wf.mark_processing(db, candidate, req)
    db.commit()

    adapter = get_rag_adapter()
    result = await adapter.submit(metadata)
    if result.success:
        req = wf.mark_indexed(db, candidate, req, result.external_ingestion_id)
    else:
        req = wf.mark_failed(db, candidate, req, result.error or "unknown adapter error")
    db.commit()
    db.refresh(req)
    return req


@router.post("/reject", response_model=RagIngestionRequestOut)
def reject(
    payload: RagRejectRequest,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    req = _handle(wf.reject, db, candidate, principal.subject, payload.reason)
    db.commit()
    db.refresh(req)
    return req


@router.post("/retry", response_model=RagIngestionRequestOut)
async def retry(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    """Same PROVISIONAL synchronous inline-adapter pattern as `approve`
    above (see the module docstring) -- not the final production design
    for a real, potentially slow adapter."""
    req = wf.get_or_create_request(db, candidate)
    req = _handle(wf.retry, db, candidate, req, principal.subject)
    req = wf.mark_processing(db, candidate, req)
    db.commit()

    adapter = get_rag_adapter()
    result = await adapter.submit(req.ingestion_metadata or _build_rag_metadata(candidate, principal.subject, db))
    if result.success:
        req = wf.mark_indexed(db, candidate, req, result.external_ingestion_id)
    else:
        req = wf.mark_failed(db, candidate, req, result.error or "unknown adapter error")
    db.commit()
    db.refresh(req)
    return req


@router.get("/status", response_model=RagIngestionRequestOut)
def get_status(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    req = wf.get_or_create_request(db, candidate)
    return req


def _build_rag_metadata(candidate: DiscoveryCandidate, approved_by: str, db: Session | None = None) -> dict:
    """
    RAG ingestion metadata payload (spec #26).

    CMT-specific overhaul, Phase 7 (RAG handoff): now includes a
    `full_text` block when a document has been acquired for this
    candidate (app/models/document.py / app/services/fulltext). This is
    ADDITIVE metadata only -- it does not change when/how `adapter.submit`
    is called, does not activate the provisional HTTP adapter, and does
    not touch the RAG approval state machine. The real CMT Veda RAG
    ingestion contract is still unconfirmed (see app/services/rag_adapter.py),
    so this is prepared for a verified contract to consume, not proof one
    exists yet.

    PROVENANCE SEPARATION (corrective prompt #8/#9 -- "the item sent to
    the RAG must be the original source/document, not the AI-written
    news article"): every field in this payload is sourced from
    `DiscoveryCandidate` (the scientific record itself: title, authors,
    journal, DOI/PMID/trial ID, subtypes, genes) and, when acquired, the
    `DiscoveryDocument` row (the original full-text file's own
    reference/hash/provenance). This function deliberately never reads
    `DiscoveryEditorialDraft` (the AI-written headline/summary that
    Gemini produces for the newsletter/website) -- that model is not
    imported here and none of its fields appear in this payload. The
    editorial draft is presented in CMT Veda / the newsletter as its own
    separate artifact; it is not, and must never become, the document
    the RAG is grounded in. See tests/test_rag_provenance.py for an
    explicit regression test asserting this.
    """
    from datetime import datetime, timezone

    metadata = {
        "document_type": candidate.content_type,
        "title": candidate.title,
        "authors": candidate.authors,
        "journal": candidate.journal,
        "publication_date": candidate.original_date.isoformat() if candidate.original_date else None,
        "doi": candidate.doi,
        "pmid": candidate.pmid,
        "clinical_trial_id": candidate.clinical_trial_id,
        "cmt_subtypes": candidate.cmt_subtypes,
        "genes": candidate.genes,
        "study_type": candidate.study_type,
        "source": candidate.source_name,
        "source_tier": candidate.source_tier,
        "discovered_by": "cmt-veda-discovery-engine",
        "approved_by": approved_by,
        "approval_time": datetime.now(timezone.utc).isoformat(),
        "knowledge_version": "v1",
        "full_text_available": candidate.full_text_available,
    }

    if db is not None and candidate.full_text_available:
        from sqlalchemy import select

        from app.models.document import DiscoveryDocument

        doc = db.execute(
            select(DiscoveryDocument)
            .where(DiscoveryDocument.candidate_id == candidate.id, DiscoveryDocument.retrieval_status == "acquired")
            .order_by(DiscoveryDocument.created_at.desc())
        ).scalars().first()
        if doc:
            metadata["full_text"] = {
                "document_ref": doc.document_ref,
                "mime_type": doc.mime_type,
                "full_text_format": doc.full_text_format,
                "pdf_available": doc.pdf_available,
                "content_hash": doc.content_hash,
                "full_text_source": doc.full_text_source,
                "license_provenance": doc.license_provenance,
            }

    return metadata
