"""
Candidate Workspace API (Final Functional Requirements, spec item 4).

Entirely read-only: every lookup here is a plain SELECT against a table
Discovery already owns. Unlike the granular `newsletter`/`rag` routers,
this endpoint deliberately does NOT call `newsletter_workflow.
get_or_create_item` or any RAG `get_or_create_request` equivalent --
those create a row as a side effect of being called, which is correct
for their own granular "status" endpoints but wrong for a read-only
aggregation: viewing a candidate's workspace must never itself create a
`NewsletterItem`/`RagIngestionRequest` for a candidate nobody has
selected for either workflow yet. A plain SELECT returning None is used
instead; the nested `newsletter`/`rag` blocks are simply omitted (null)
until one of the dedicated workflow endpoints actually creates one.

A small, fixed number of queries regardless of how much is nested
(candidate lookup + 6 further single-row/bounded lookups) -- no
per-related-row N+1 loop anywhere in this handler.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import Principal, require_any_authenticated
from app.models.analysis import DiscoveryAnalysis
from app.models.audit import DiscoveryAuditLog
from app.models.candidate import DiscoveryCandidate
from app.models.editorial import DiscoveryEditorialDraft
from app.models.newsletter import NewsletterItem
from app.models.rag import RagIngestionRequest
from app.models.source import DiscoverySource
from app.schemas.workspace import CandidateWorkspaceOut
from app.services.document_service import get_representative_document

router = APIRouter(tags=["workspace"])


@router.get("/candidates/{candidate_id}/workspace", response_model=CandidateWorkspaceOut)
def get_candidate_workspace(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    source = db.get(DiscoverySource, candidate.source_id) if candidate.source_id else None

    analysis = db.execute(
        select(DiscoveryAnalysis)
        .where(DiscoveryAnalysis.candidate_id == candidate.id, DiscoveryAnalysis.is_latest.is_(True))
        .order_by(DiscoveryAnalysis.generated_at.desc())
    ).scalars().first()

    document = get_representative_document(db, candidate.id)

    editorial_draft = db.execute(
        select(DiscoveryEditorialDraft)
        .where(DiscoveryEditorialDraft.candidate_id == candidate.id, DiscoveryEditorialDraft.is_current.is_(True))
    ).scalars().first()

    newsletter_item = db.execute(
        select(NewsletterItem).where(NewsletterItem.candidate_id == candidate.id)
    ).scalars().first()

    rag_request = db.execute(
        select(RagIngestionRequest).where(RagIngestionRequest.candidate_id == candidate.id)
    ).scalars().first()

    recent_audit = db.execute(
        select(DiscoveryAuditLog)
        .where(DiscoveryAuditLog.candidate_id == candidate.id)
        .order_by(DiscoveryAuditLog.performed_at.desc())
        .limit(20)
    ).scalars().all()

    return CandidateWorkspaceOut(
        candidate=candidate,
        source=source,
        analysis=analysis,
        document=document,
        editorial_draft=editorial_draft,
        newsletter=newsletter_item,
        rag=rag_request,
        recent_audit=list(recent_audit),
    )
