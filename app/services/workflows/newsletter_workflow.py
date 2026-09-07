"""
Newsletter workflow state machine (spec #22, #50).

    not_selected -> selected -> drafted -> under_review -> approved -> scheduled -> published
                                    \\-> rejected -> archived
    (any non-terminal state) -> archived

Every transition is a dedicated function, never a generic "set status to
X" endpoint (spec #50). AI/automated code can only reach `drafted`
(when an editorial draft is generated); everything from `under_review`
onward requires a human `performed_by` supplied by the API layer after
role-checking (spec #22: "AI cannot independently approve, schedule, or
publish").

The authoritative status lives on `NewsletterItem.status`; this module is
also responsible for keeping `DiscoveryCandidate.newsletter_status` (the
denormalized read copy used by candidate list/filter APIs) in sync.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import DiscoveryCandidate
from app.models.enums import AuditAction, NewsletterStatus
from app.models.newsletter import NewsletterItem, NewsletterPublication
from app.services.audit_service import write_audit_log

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    NewsletterStatus.not_selected.value: {NewsletterStatus.selected.value},
    NewsletterStatus.selected.value: {NewsletterStatus.drafted.value, NewsletterStatus.archived.value},
    NewsletterStatus.drafted.value: {NewsletterStatus.under_review.value, NewsletterStatus.archived.value},
    NewsletterStatus.under_review.value: {
        NewsletterStatus.approved.value,
        NewsletterStatus.rejected.value,
        NewsletterStatus.archived.value,
    },
    NewsletterStatus.approved.value: {NewsletterStatus.scheduled.value, NewsletterStatus.archived.value},
    NewsletterStatus.scheduled.value: {NewsletterStatus.published.value, NewsletterStatus.archived.value},
    NewsletterStatus.published.value: {NewsletterStatus.archived.value},
    NewsletterStatus.rejected.value: {NewsletterStatus.archived.value, NewsletterStatus.selected.value},
    NewsletterStatus.archived.value: set(),
}


class InvalidTransition(Exception):
    pass


def _transition(item: NewsletterItem, candidate: DiscoveryCandidate, new_status: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(item.status, set())
    if new_status not in allowed:
        raise InvalidTransition(f"Cannot move newsletter item from '{item.status}' to '{new_status}'")
    item.status = new_status
    candidate.newsletter_status = new_status


def get_or_create_item(db: Session, candidate: DiscoveryCandidate) -> NewsletterItem:
    item = db.execute(select(NewsletterItem).where(NewsletterItem.candidate_id == candidate.id)).scalars().first()
    if item is None:
        item = NewsletterItem(candidate_id=candidate.id, status=NewsletterStatus.not_selected.value)
        db.add(item)
        db.flush()
    return item


def select_for_newsletter(db: Session, candidate: DiscoveryCandidate, performed_by: str) -> NewsletterItem:
    item = get_or_create_item(db, candidate)
    _transition(item, candidate, NewsletterStatus.selected.value)
    item.selected_by = performed_by
    item.selected_at = datetime.now(timezone.utc)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.newsletter_selected, performed_by=performed_by)
    return item


def mark_drafted(db: Session, candidate: DiscoveryCandidate, editorial_draft_id: uuid.UUID) -> NewsletterItem:
    """Called automatically once an editorial draft exists for a selected item."""
    item = get_or_create_item(db, candidate)
    if item.status == NewsletterStatus.selected.value:
        _transition(item, candidate, NewsletterStatus.drafted.value)
    item.editorial_draft_id = editorial_draft_id
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.draft_generated, performed_by="veda_intelligence")
    return item


def submit_for_review(db: Session, candidate: DiscoveryCandidate, performed_by: str) -> NewsletterItem:
    item = get_or_create_item(db, candidate)
    _transition(item, candidate, NewsletterStatus.under_review.value)
    item.reviewed_by = performed_by
    item.reviewed_at = datetime.now(timezone.utc)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.opened_for_review, performed_by=performed_by)
    return item


def approve(db: Session, candidate: DiscoveryCandidate, performed_by: str) -> NewsletterItem:
    item = get_or_create_item(db, candidate)
    _transition(item, candidate, NewsletterStatus.approved.value)
    item.approved_by = performed_by
    item.approved_at = datetime.now(timezone.utc)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.newsletter_approved, performed_by=performed_by)
    return item


def reject(db: Session, candidate: DiscoveryCandidate, performed_by: str, reason: str) -> NewsletterItem:
    item = get_or_create_item(db, candidate)
    _transition(item, candidate, NewsletterStatus.rejected.value)
    item.rejected_by = performed_by
    item.rejected_at = datetime.now(timezone.utc)
    item.rejection_reason = reason
    write_audit_log(
        db, candidate_id=candidate.id, action=AuditAction.newsletter_rejected, performed_by=performed_by,
        notes=reason,
    )
    return item


def schedule(
    db: Session,
    candidate: DiscoveryCandidate,
    performed_by: str,
    scheduled_for: datetime,
    publication: NewsletterPublication,
) -> NewsletterItem:
    item = get_or_create_item(db, candidate)
    _transition(item, candidate, NewsletterStatus.scheduled.value)
    item.scheduled_for = scheduled_for
    if str(item.id) not in publication.item_ids:
        publication.item_ids = [*publication.item_ids, str(item.id)]
    write_audit_log(
        db, candidate_id=candidate.id, action=AuditAction.scheduled, performed_by=performed_by,
        notes=f"publication_id={publication.id}",
    )
    return item


def publish(db: Session, candidate: DiscoveryCandidate, item: NewsletterItem, performed_by: str) -> NewsletterItem:
    _transition(item, candidate, NewsletterStatus.published.value)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.published, performed_by=performed_by)
    return item


def archive(db: Session, candidate: DiscoveryCandidate, performed_by: str) -> NewsletterItem:
    item = get_or_create_item(db, candidate)
    _transition(item, candidate, NewsletterStatus.archived.value)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.archived, performed_by=performed_by)
    return item
