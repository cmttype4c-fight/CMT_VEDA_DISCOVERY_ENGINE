"""
Newsletter workflow API (spec #22, #36).

Every transition here requires a human principal (reviewer or admin) --
enforced by the auth dependency, never by the frontend hiding a button
(spec #40). `select`/`review`/`approve`/`reject` are reviewer-or-admin;
`schedule`/`publish` require admin, matching "AI cannot independently
approve, schedule, or publish" plus the extra weight of actually sending
something out.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import Principal, require_admin, require_reviewer_or_admin
from app.models.candidate import DiscoveryCandidate
from app.models.newsletter import NewsletterPublication
from app.schemas.newsletter import (
    NewsletterItemOut,
    NewsletterPublicationCreate,
    NewsletterPublicationOut,
    NewsletterRejectRequest,
    NewsletterScheduleRequest,
)
from app.services.workflows import newsletter_workflow as wf
from app.services.workflows.newsletter_workflow import InvalidTransition

router = APIRouter(tags=["newsletter"])


def _handle_transition(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except InvalidTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/candidates/{candidate_id}/newsletter/select", response_model=NewsletterItemOut)
def select_for_newsletter(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    item = _handle_transition(wf.select_for_newsletter, db, candidate, principal.subject)
    db.commit()
    db.refresh(item)
    return item


@router.post("/candidates/{candidate_id}/newsletter/review", response_model=NewsletterItemOut)
def submit_for_review(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    item = _handle_transition(wf.submit_for_review, db, candidate, principal.subject)
    db.commit()
    db.refresh(item)
    return item


@router.post("/candidates/{candidate_id}/newsletter/approve", response_model=NewsletterItemOut)
def approve_newsletter_item(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    item = _handle_transition(wf.approve, db, candidate, principal.subject)
    db.commit()
    db.refresh(item)
    return item


@router.post("/candidates/{candidate_id}/newsletter/reject", response_model=NewsletterItemOut)
def reject_newsletter_item(
    payload: NewsletterRejectRequest,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    item = _handle_transition(wf.reject, db, candidate, principal.subject, payload.reason)
    db.commit()
    db.refresh(item)
    return item


@router.post("/candidates/{candidate_id}/newsletter/schedule", response_model=NewsletterItemOut)
def schedule_newsletter_item(
    payload: NewsletterScheduleRequest,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    publication = None
    if payload.publication_id:
        publication = db.get(NewsletterPublication, payload.publication_id)
        if publication is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publication not found")
    else:
        publication = NewsletterPublication(
            title=f"Newsletter - {payload.scheduled_for.date()}",
            status="scheduled",
            scheduled_for=payload.scheduled_for,
            created_by=principal.subject,
        )
        db.add(publication)
        db.flush()

    item = _handle_transition(wf.schedule, db, candidate, principal.subject, payload.scheduled_for, publication)
    db.commit()
    db.refresh(item)
    return item


@router.post("/candidates/{candidate_id}/newsletter/archive", response_model=NewsletterItemOut)
def archive_newsletter_item(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_admin),
):
    item = _handle_transition(wf.archive, db, candidate, principal.subject)
    db.commit()
    db.refresh(item)
    return item


@router.post("/newsletter-publications", response_model=NewsletterPublicationOut, status_code=201)
def create_publication(
    payload: NewsletterPublicationCreate, db: Session = Depends(get_db), principal: Principal = Depends(require_admin)
):
    publication = NewsletterPublication(
        title=payload.title, status="scheduled", scheduled_for=payload.scheduled_for, created_by=principal.subject
    )
    db.add(publication)
    db.commit()
    db.refresh(publication)
    return publication


@router.post("/newsletter-publications/{publication_id}/publish", response_model=NewsletterPublicationOut)
def publish_publication(
    publication_id: uuid.UUID, db: Session = Depends(get_db), principal: Principal = Depends(require_admin)
):
    from datetime import datetime, timezone

    from app.models.newsletter import NewsletterItem

    publication = db.get(NewsletterPublication, publication_id)
    if publication is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publication not found")

    for item_id in publication.item_ids:
        item = db.get(NewsletterItem, uuid.UUID(item_id))
        if item is None:
            continue
        candidate = db.get(DiscoveryCandidate, item.candidate_id)
        _handle_transition(wf.publish, db, candidate, item, principal.subject)

    publication.status = "published"
    publication.published_at = datetime.now(timezone.utc)
    publication.published_by = principal.subject
    db.commit()
    db.refresh(publication)
    return publication
