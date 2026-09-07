"""Editorial draft API (spec #20-21, #36)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import Principal, require_admin, require_any_authenticated, require_reviewer_or_admin
from app.models.candidate import DiscoveryCandidate
from app.models.editorial import DiscoveryEditorialDraft
from app.schemas.editorial import EditorialDraftOut, EditorialDraftUpdate, EditorialGenerateRequest
from app.services.intelligence.editorial_service import generate_editorial_draft, update_draft_manually

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
    db.commit()
    db.refresh(new_draft)
    return new_draft
