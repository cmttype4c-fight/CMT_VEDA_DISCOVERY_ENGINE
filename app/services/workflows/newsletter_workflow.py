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
    NewsletterStatus.scheduled.value: {
        NewsletterStatus.published.value,
        NewsletterStatus.archived.value,
        # Added for CMT Veda compatibility (DELETE .../schedule ->
        # unschedule()): the smallest valid edge needed to let a
        # scheduled item be pulled back for further editing rather than
        # only ever moving forward to published or terminally archived.
        NewsletterStatus.approved.value,
    },
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


def mark_draft_saved_manually(
    db: Session, candidate: DiscoveryCandidate, editorial_draft_id: uuid.UUID, performed_by: str,
) -> NewsletterItem:
    """
    Manual-save counterpart to `mark_drafted` above (CMT Veda "Save Draft"
    fix). A human editor saving a draft via `PATCH .../editorial-draft` (or
    its `PATCH .../editorial` compatibility alias) must have the same
    selected -> drafted effect that the automated worker pipeline already
    gets when it calls `mark_drafted` -- otherwise a manually-saved draft
    never associates with the `NewsletterItem` and the candidate is stuck
    showing `selected` forever.

    Deliberately NOT a call to `mark_drafted` itself, for two reasons:

    1. Audit accuracy: `mark_drafted` hardcodes
       `performed_by="veda_intelligence"` and `action=AuditAction.draft_generated`
       unconditionally, which is correct for the AI/automated path but would
       be a false audit record for a human editor's manual save. This
       function instead uses the real `performed_by` (the authenticated
       principal who called the PATCH endpoint) and `AuditAction.edited`
       (an action already defined on the enum but, until this fix, never
       used anywhere -- exactly the "appropriate event for the draft being
       saved/associated" the fix calls for, kept distinct from the AI
       path's `draft_generated` semantics).
    2. Same transition guard as `mark_drafted`: only `selected -> drafted`
       is performed, and only when the item is currently `selected`. If the
       item is already `drafted` (re-saving a draft), already
       `under_review`, `approved`, `scheduled`, or `published`, this is a
       no-op with respect to `status` -- it never regresses a later state
       and never reaches into `_transition`/`ALLOWED_TRANSITIONS` for any
       other edge. The association (`editorial_draft_id`) is always
       refreshed regardless of status, since pointing at the latest saved
       draft is a simple association, not a state transition, and is safe
       to update no matter what stage review has reached.
    """
    item = get_or_create_item(db, candidate)
    if item.status == NewsletterStatus.selected.value:
        _transition(item, candidate, NewsletterStatus.drafted.value)
    item.editorial_draft_id = editorial_draft_id
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.edited, performed_by=performed_by)
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


def unschedule(db: Session, candidate: DiscoveryCandidate, performed_by: str) -> NewsletterItem:
    """
    CMT Veda compatibility (`DELETE .../schedule`): pulls a `scheduled`
    item back to `approved` so it can be edited or rescheduled. This
    edge did not exist in the original state machine -- the smallest
    valid addition needed for this one operation is the single
    `scheduled -> approved` entry in ALLOWED_TRANSITIONS above; nothing
    else about the state machine changes.

    Also detaches the item from any publication's `item_ids` it was
    attached to via `schedule()` -- otherwise a later batch-publish of
    that publication would still reference this item and fail with
    InvalidTransition, since it is no longer `scheduled`.
    """
    item = get_or_create_item(db, candidate)
    _transition(item, candidate, NewsletterStatus.approved.value)
    item.scheduled_for = None

    publications = db.execute(select(NewsletterPublication)).scalars().all()
    for publication in publications:
        if str(item.id) in publication.item_ids:
            publication.item_ids = [i for i in publication.item_ids if i != str(item.id)]

    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.unscheduled, performed_by=performed_by)
    return item


def publish(db: Session, candidate: DiscoveryCandidate, item: NewsletterItem, performed_by: str) -> NewsletterItem:
    _transition(item, candidate, NewsletterStatus.published.value)
    # CMT Veda final-contract pass (spec item 10): `GET /newsletter/published`
    # needs a stable "when was this actually published" moment to sort by.
    # Set exactly once, here, at the single call site that ever performs
    # this transition -- never touched again afterwards (not even by a
    # later set_section() call), so it stays a true publish timestamp.
    item.published_at = datetime.now(timezone.utc)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.published, performed_by=performed_by)
    return item


def set_section(db: Session, candidate: DiscoveryCandidate, section: str, performed_by: str) -> NewsletterItem:
    """
    Persist the Newsletter section/destination for a candidate's item
    (spec item 9 -- "persistent Newsletter distribution: the selected
    Newsletter section/destination must not exist only in browser
    state").

    Deliberately NOT a state-machine transition: it never calls
    `_transition`/touches `ALLOWED_TRANSITIONS`, never changes `status`,
    and can be called at ANY status (including `not_selected`, so an
    editor can pre-stage a destination before the item is even selected).
    This is a plain metadata assignment, the same category of operation
    as `mark_draft_saved_manually`'s `editorial_draft_id` association --
    safe to call no matter what stage review has reached.
    """
    item = get_or_create_item(db, candidate)
    old_section = item.section
    item.section = section
    write_audit_log(
        db, candidate_id=candidate.id, action=AuditAction.section_assigned, performed_by=performed_by,
        old_value={"section": old_section}, new_value={"section": section},
    )
    return item


def archive(db: Session, candidate: DiscoveryCandidate, performed_by: str) -> NewsletterItem:
    item = get_or_create_item(db, candidate)
    _transition(item, candidate, NewsletterStatus.archived.value)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.archived, performed_by=performed_by)
    return item
