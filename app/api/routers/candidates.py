"""
Candidates API (spec #14, #36-39).

Supports pagination (spec #37), filtering by content_type/source/scope/
gene/subtype/topic/date/score/newsletter_status/rag_status/review_status
(spec #38), and free-text search over title/authors/DOI/PMID/
ClinicalTrials ID/source (spec #39).

Updates only ever go through `apply_manual_override` (spec #29, #50) --
there is deliberately no generic PATCH that lets a client set arbitrary
fields like `newsletter_status` directly; those are only ever changed via
the dedicated newsletter/rag workflow endpoints.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import Principal, require_admin, require_any_authenticated
from app.models.candidate import DiscoveryCandidate
from app.models.enums import NEWSLETTER_STATUS_INPUT_ALIASES
from app.schemas.candidate import CandidateOut, CandidateOverrideUpdate
from app.schemas.common import Page
from app.services.candidate_service import apply_manual_override

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.get("", response_model=Page[CandidateOut])
def list_candidates(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    page: int | None = Query(
        None, ge=1,
        description="CMT Veda final-contract pass (spec item 3): convenience alias for Veda-v1's "
        "page/page_size pagination style. Canonical contract remains limit/offset (see "
        "docs/API_CONTRACTS.md) -- the response envelope always returns limit/offset/total/has_more "
        "regardless of which input style was used. If `page` is given, it and `page_size` together "
        "take precedence over `limit`/`offset`.",
    ),
    page_size: int | None = Query(None, ge=1, le=200, description="Paired with `page`; see `page`."),
    content_type: str | None = None,
    source_id: uuid.UUID | None = None,
    scope: str | None = None,
    gene: str | None = None,
    subtype: str | None = None,
    topic: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    newsletter_status: str | None = Query(
        None,
        description="Canonical NewsletterStatus value (e.g. 'drafted'). The alias 'draft' is also "
        "accepted and normalized to 'drafted' -- see app/models/enums.py's "
        "NEWSLETTER_STATUS_INPUT_ALIASES and docs/API_CONTRACTS.md item 3.",
    ),
    rag_status: str | None = None,
    q: str | None = Query(None, description="Free-text search over title/authors/doi/pmid/ctid/source"),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    # CMT Veda final-contract pass (spec item 3): resolve the Veda
    # page/page_size vs Discovery limit/offset mismatch by accepting
    # EITHER on input -- `page`/`page_size`, when given, are converted to
    # the canonical limit/offset right here, once, so every line below
    # (and the response envelope) only ever deals with limit/offset. This
    # is additive: a client that keeps sending limit/offset is completely
    # unaffected.
    if page is not None:
        limit = page_size or limit
        offset = (page - 1) * limit

    # Resolve the Veda "draft" vs Discovery "drafted" terminology mismatch
    # (spec item 3) for this one input point -- never written back to
    # storage, never reflected in any response (every output still uses
    # the canonical NewsletterStatus value).
    if newsletter_status:
        newsletter_status = NEWSLETTER_STATUS_INPUT_ALIASES.get(newsletter_status, newsletter_status)

    query = select(DiscoveryCandidate)

    if content_type:
        query = query.where(DiscoveryCandidate.content_type == content_type)
    if source_id:
        query = query.where(DiscoveryCandidate.source_id == source_id)
    if scope:
        query = query.where(DiscoveryCandidate.scope == scope)
    if newsletter_status:
        query = query.where(DiscoveryCandidate.newsletter_status == newsletter_status)
    if rag_status:
        query = query.where(DiscoveryCandidate.rag_status == rag_status)
    if date_from:
        query = query.where(DiscoveryCandidate.original_date >= date_from)
    if date_to:
        query = query.where(DiscoveryCandidate.original_date <= date_to)

    # JSON-array membership filters (gene/subtype/topic): fetched in Python
    # after a bounded pre-filter since portable JSON "contains" queries
    # differ between SQLite (tests) and Postgres (production). For very
    # large tables this can be swapped for a Postgres JSONB @> filter
    # (a follow-up SQL-side optimization noted in IMPLEMENTATION_STATUS.md).
    rows = db.execute(query.order_by(DiscoveryCandidate.discovered_at.desc())).scalars().all()

    if gene:
        rows = [r for r in rows if gene in (r.genes or [])]
    if subtype:
        rows = [r for r in rows if subtype in (r.cmt_subtypes or [])]
    if topic:
        rows = [r for r in rows if topic in (r.topics or [])]
    if q:
        q_lower = q.lower()

        def _matches(c: DiscoveryCandidate) -> bool:
            haystacks = [
                c.title or "",
                " ".join(c.authors or []),
                c.doi or "",
                c.pmid or "",
                c.clinical_trial_id or "",
                c.source_name or "",
            ]
            return any(q_lower in h.lower() for h in haystacks)

        rows = [r for r in rows if _matches(r)]

    total = len(rows)
    page_rows = rows[offset : offset + limit]
    return Page(items=page_rows, total=total, limit=limit, offset=offset, has_more=offset + len(page_rows) < total)


@router.get("/{candidate_id}", response_model=CandidateOut)
def get_candidate(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404), _: Principal = Depends(require_any_authenticated)
):
    return candidate


@router.patch("/{candidate_id}", response_model=CandidateOut)
def override_candidate(
    payload: CandidateOverrideUpdate,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    """Manual override of AI classification (spec #29). Administrator only."""
    apply_manual_override(db, candidate, payload, performed_by=principal.subject)
    db.commit()
    db.refresh(candidate)
    return candidate
