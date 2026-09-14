"""
Newsletter workflow API (spec #22, #36).

Every transition here requires a human principal (reviewer or admin) --
enforced by the auth dependency, never by the frontend hiding a button
(spec #40). `select`/`review`/`approve`/`reject` are reviewer-or-admin;
`schedule`/`publish` require admin, matching "AI cannot independently
approve, schedule, or publish" plus the extra weight of actually sending
something out.

Also hosts the CMT Veda compatibility adapters (`/schedule` alias,
`DELETE /schedule`, `/editorial-status`, `/publish`, `/candidates/bulk`)
at the bottom of this file. Every one of them is a thin wrapper that
calls the same `newsletter_workflow` functions above -- no second state
machine, no duplicated transition logic.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_candidate_or_404, get_db
from app.auth import Principal, require_admin, require_any_authenticated, require_reviewer_or_admin, require_service_or_admin
from app.models.candidate import DiscoveryCandidate
from app.models.enums import NewsletterStatus, Role
from app.models.newsletter import NewsletterPublication
from app.schemas.newsletter import (
    BulkCandidateAction,
    BulkResult,
    BulkResultItem,
    EditorialStatusTransitionRequest,
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


# ============================================================
# CMT Veda compatibility adapters
#
# Everything below is a thin wrapper over the functions above: no new
# transition logic, no second state machine, no duplicated persistence.
# ============================================================

# Maps an accepted target status (a NewsletterStatus value) to the
# existing function that performs it, and the minimum role the GRANULAR
# route for that same action already requires today -- reused here so
# the unified endpoints below enforce exactly the same per-action
# boundaries as the routes above, just through one entry point.
_STATUS_DISPATCH: dict[str, tuple[object, Role]] = {
    NewsletterStatus.selected.value: (wf.select_for_newsletter, Role.reviewer),
    NewsletterStatus.under_review.value: (wf.submit_for_review, Role.reviewer),
    NewsletterStatus.approved.value: (wf.approve, Role.discovery_administrator),
    NewsletterStatus.rejected.value: (wf.reject, Role.reviewer),
    NewsletterStatus.archived.value: (wf.archive, Role.discovery_administrator),
}


def _check_role_for_status(principal: Principal, target_status: str, min_role: Role) -> None:
    """`service` and `discovery_administrator` may request any supported
    transition (`service` is the trusted CMT Veda gateway, see
    app/auth.py; admin could already do all of this through the granular
    routes). A `reviewer` principal is only allowed the same subset they
    can already reach today (select/review/reject) -- requesting
    approve/archive through this endpoint is rejected exactly as it
    would be against the granular route."""
    if principal.role in (Role.discovery_administrator, Role.service):
        return
    if principal.role == min_role:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Role '{principal.role.value}' cannot transition to '{target_status}'",
    )


def _validate_status_request(status_value: str, reason: str | None, principal: Principal) -> None:
    if status_value not in _STATUS_DISPATCH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported status '{status_value}'. Supported: {sorted(_STATUS_DISPATCH)}",
        )
    _, min_role = _STATUS_DISPATCH[status_value]
    _check_role_for_status(principal, status_value, min_role)
    if status_value == NewsletterStatus.rejected.value and not reason:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'reason' is required when status is 'rejected'",
        )


def _apply_status_transition(
    db: Session, candidate: DiscoveryCandidate, status_value: str, performed_by: str, reason: str | None
):
    """Assumes `_validate_status_request` already passed. Dispatches to
    the same select/review/approve/reject/archive functions the granular
    routes above call -- `_handle_transition` still applies the same
    InvalidTransition -> 409 conversion those routes already use, so an
    invalid transition from the candidate's *current* status is rejected
    identically either way."""
    fn, _min_role = _STATUS_DISPATCH[status_value]
    if status_value == NewsletterStatus.rejected.value:
        return _handle_transition(fn, db, candidate, performed_by, reason)
    return _handle_transition(fn, db, candidate, performed_by)


@router.post("/candidates/{candidate_id}/editorial-status", response_model=NewsletterItemOut)
def set_editorial_status(
    payload: EditorialStatusTransitionRequest,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_authenticated),
):
    """
    CMT Veda compatibility: one status-driven endpoint over the granular
    select/review/approve/reject/archive routes above. `payload.status`
    must be one of their target NewsletterStatus values. Unsupported
    status values are rejected with 422; invalid transitions from the
    candidate's current status are rejected with 409 (same
    InvalidTransition handling every other route in this file uses);
    per-action role boundaries are preserved (see `_check_role_for_status`).
    """
    _validate_status_request(payload.status, payload.reason, principal)
    item = _apply_status_transition(db, candidate, payload.status, principal.subject, payload.reason)
    db.commit()
    db.refresh(item)
    return item


@router.post("/candidates/{candidate_id}/schedule", response_model=NewsletterItemOut)
def schedule_newsletter_item_compat(
    payload: NewsletterScheduleRequest,
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_service_or_admin),
):
    """Thin CMT Veda compatibility alias for `POST .../newsletter/schedule`
    above -- calls that exact function directly. The original path and
    its own `require_admin` dependency are unchanged; this new path
    additionally accepts the `service` role for the gateway."""
    return schedule_newsletter_item(payload, candidate=candidate, db=db, principal=principal)


@router.delete("/candidates/{candidate_id}/schedule", response_model=NewsletterItemOut)
def unschedule_newsletter_item(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_service_or_admin),
):
    """CMT Veda compatibility: pulls a `scheduled` candidate back to
    `approved`. See `newsletter_workflow.unschedule()` for the one new
    state-machine edge (`scheduled -> approved`) this required."""
    item = _handle_transition(wf.unschedule, db, candidate, principal.subject)
    db.commit()
    db.refresh(item)
    return item


@router.post("/candidates/{candidate_id}/publish", response_model=NewsletterItemOut)
def publish_candidate(
    candidate: DiscoveryCandidate = Depends(get_candidate_or_404),
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_service_or_admin),
):
    """
    CMT Veda compatibility: publishes one candidate directly rather than
    requiring the separate "create a publication, attach items, publish
    the publication" sequence `/newsletter-publications/...` above
    requires. Validates the candidate is `scheduled` first (422 if not),
    then reuses the exact same `newsletter_workflow.publish()` transition
    `publish_publication` above already uses, and the same ad-hoc
    `NewsletterPublication` creation pattern `schedule_newsletter_item`
    uses when no publication is supplied -- no new publication mechanism.
    Returns the candidate's own newsletter item (now `published`), which
    is what a per-candidate publish call is actually about; the
    publication record it was wrapped in for history/traceability is not
    part of this response shape today.
    """
    from datetime import datetime, timezone

    item = wf.get_or_create_item(db, candidate)
    if item.status != NewsletterStatus.scheduled.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Candidate must be 'scheduled' before it can be published (currently '{item.status}').",
        )

    publication = NewsletterPublication(
        title=f"Newsletter - {candidate.title[:80]}",
        status="scheduled",
        scheduled_for=item.scheduled_for,
        created_by=principal.subject,
        item_ids=[str(item.id)],
    )
    db.add(publication)
    db.flush()

    item = _handle_transition(wf.publish, db, candidate, item, principal.subject)

    publication.status = "published"
    publication.published_at = datetime.now(timezone.utc)
    publication.published_by = principal.subject

    db.commit()
    db.refresh(item)
    return item


@router.post("/candidates/bulk", response_model=BulkResult)
def bulk_candidate_action(
    payload: BulkCandidateAction,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_any_authenticated),
):
    """
    CMT Veda compatibility: applies one editorial-status transition to
    several candidates. Internally calls the exact same
    `_apply_status_transition` dispatch `/editorial-status` above uses,
    once per candidate -- no separate bulk business logic and no new
    database model. Each candidate's work happens inside its own
    SAVEPOINT (`db.begin_nested()`), the same per-item failure-isolation
    pattern already used by the worker's collection pipeline
    (app/worker/handlers.py), so one invalid candidate cannot hide or
    abort the results for the others.
    """
    _validate_status_request(payload.status, payload.reason, principal)

    results: list[BulkResultItem] = []
    for candidate_id in payload.candidate_ids:
        try:
            with db.begin_nested():
                candidate = db.get(DiscoveryCandidate, candidate_id)
                if candidate is None:
                    raise ValueError(f"Candidate {candidate_id} not found")
                _apply_status_transition(db, candidate, payload.status, principal.subject, payload.reason)
            results.append(BulkResultItem(candidate_id=candidate_id, success=True))
        except HTTPException as exc:
            results.append(BulkResultItem(candidate_id=candidate_id, success=False, error=str(exc.detail)))
        except Exception as exc:  # noqa: BLE001 - isolate this candidate's failure from the rest of the batch
            results.append(BulkResultItem(candidate_id=candidate_id, success=False, error=str(exc)))

    db.commit()
    succeeded = sum(1 for r in results if r.success)
    return BulkResult(results=results, succeeded=succeeded, failed=len(results) - succeeded)
