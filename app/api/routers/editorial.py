"""Editorial draft API (spec #20-21, #36)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import (
    Principal,
    require_admin,
    require_any_authenticated,
    require_reviewer_or_admin,
    require_service_or_reviewer_or_admin,
)
from app.models.candidate import DiscoveryCandidate
from app.models.editorial import DiscoveryEditorialDraft
from app.models.newsletter import NewsletterItem
from app.schemas.editorial import EditorialDraftOut, EditorialDraftUpdate, EditorialGenerateRequest
from app.services.intelligence.editorial_service import generate_editorial_draft, update_draft_manually
from app.services.workflows.newsletter_workflow import mark_draft_saved_manually

router = APIRouter(tags=["editorial"])


@router.post("/candidates/{candidate_id}/editorial-draft", response_model=EditorialDraftOut)
async def generate_draft(
    payload: EditorialGenerateRequest = EditorialGenerateRequest(),
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
):
    if not payload.force_regenerate:
        existing = db.execute(
            select(DiscoveryEditorialDraft).where(
                DiscoveryEditorialDraft.candidate_id == candidate.id, DiscoveryEditorialDraft.is_current.is_(True)
            )
        ).scalars().first()
        if existing:
            return existing

    draft = await generate_editorial_draft(db, candidate)
    db.commit()
    db.refresh(draft)
    return draft


@router.get("/candidates/{candidate_id}/editorial-draft", response_model=EditorialDraftOut)
def get_current_draft(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    draft = db.execute(
        select(DiscoveryEditorialDraft).where(
            DiscoveryEditorialDraft.candidate_id == candidate.id, DiscoveryEditorialDraft.is_current.is_(True)
        )
    ).scalars().first()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No editorial draft yet for this candidate")
    return draft


@router.get("/candidates/{candidate_id}/editorial-draft/versions", response_model=list[EditorialDraftOut])
def get_draft_versions(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    return db.execute(
        select(DiscoveryEditorialDraft)
        .where(DiscoveryEditorialDraft.candidate_id == candidate.id)
        .order_by(DiscoveryEditorialDraft.draft_version.desc())
    ).scalars().all()


@router.patch("/candidates/{candidate_id}/editorial-draft", response_model=EditorialDraftOut)
def edit_draft(
    payload: EditorialDraftUpdate,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    current = db.execute(
        select(DiscoveryEditorialDraft).where(
            DiscoveryEditorialDraft.candidate_id == candidate.id, DiscoveryEditorialDraft.is_current.is_(True)
        )
    ).scalars().first()
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No editorial draft yet for this candidate")

    updates = payload.model_dump(exclude_unset=True)
    new_draft = update_draft_manually(db, current, updates, edited_by=principal.subject)

    # Manual Editorial Draft Save Workflow fix: a human saving a draft here
    # (via this endpoint or its `/editorial` CMT Veda compatibility alias
    # below, which calls this function directly) must have the same
    # selected -> drafted effect the automated worker pipeline already gets
    # from `handle_generate_editorial_draft` calling `mark_drafted`. Only
    # act when a `NewsletterItem` already exists for this candidate -- the
    # exact same guard the worker path uses (`existing_item is not None`)
    # -- so this never creates a newsletter item out of nothing for a
    # candidate that was never selected for the newsletter in the first
    # place. `mark_draft_saved_manually` itself only transitions
    # selected -> drafted (never any other edge), so a candidate already in
    # `under_review`, `approved`, `scheduled`, or `published` is left at
    # that exact status -- only its `editorial_draft_id` association is
    # refreshed to point at the draft that was just saved.
    existing_item = db.execute(
        select(NewsletterItem).where(NewsletterItem.candidate_id == candidate.id)
    ).scalars().first()
    if existing_item is not None:
        mark_draft_saved_manually(db, candidate, new_draft.id, performed_by=principal.subject)

    db.commit()
    db.refresh(new_draft)
    return new_draft


# --- CMT Veda compatibility route ---


@router.patch("/candidates/{candidate_id}/editorial", response_model=EditorialDraftOut)
def edit_draft_compat(
    payload: EditorialDraftUpdate,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_service_or_reviewer_or_admin),
):
    """
    Thin CMT Veda compatibility alias for `PATCH .../editorial-draft`
    above. Calls that exact function directly -- zero duplicated
    persistence logic, per the instruction. The original
    `/editorial-draft` path and its own `require_reviewer_or_admin`
    dependency are unchanged; this new path additionally accepts the
    `service` role so the CMT Veda gateway can call it.
    """
    return edit_draft(payload, candidate=candidate, db=db, principal=principal)
