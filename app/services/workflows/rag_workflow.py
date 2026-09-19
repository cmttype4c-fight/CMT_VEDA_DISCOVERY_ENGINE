"""
RAG workflow state machine (spec #23-24, #50-52).

    not_selected -> pending_approval -> approved -> queued -> processing -> indexed
                            \\-> rejected                           \\-> failed -> (retry) -> queued
    indexed -> removed

Rules enforced here:
  - `approve()` requires ALL FIVE verification checks to be true (spec
    #24) and is only ever called by an admin-role-checked API endpoint
    (enforced in the router, not here, but this function re-checks
    `all_checks_passed` defensively so it can never be bypassed even by a
    future internal caller).
  - AI/automated code can reach `pending_approval` (via submit) but never
    `approved`, `queued`->success, etc. -- those all require a human
    `performed_by` (spec #23: "AI cannot independently approve... Final
    approval must require appropriate administrator authority").
  - Technical failure produces `processing -> failed`, never
    `processing -> rejected` (spec #51); `rejected` only ever results from
    an explicit human `reject()` call at the `pending_approval` stage.
  - `remove()` preserves history: the RagIngestionRequest row and all
    audit log entries are kept; only `status` moves to `removed` (spec
    #52 -- archive over destructive deletion).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.candidate import DiscoveryCandidate
from app.models.enums import AuditAction, RagStatus
from app.models.rag import RagIngestionRequest
from app.services.audit_service import write_audit_log

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    RagStatus.not_selected.value: {RagStatus.pending_approval.value},
    RagStatus.pending_approval.value: {RagStatus.approved.value, RagStatus.rejected.value},
    RagStatus.approved.value: {RagStatus.queued.value},
    RagStatus.rejected.value: {RagStatus.pending_approval.value},  # resubmission after addressing concerns
    RagStatus.queued.value: {RagStatus.processing.value},
    RagStatus.processing.value: {RagStatus.indexed.value, RagStatus.failed.value},
    RagStatus.failed.value: {RagStatus.queued.value},  # retry (spec #51)
    RagStatus.indexed.value: {RagStatus.removed.value},
    RagStatus.removed.value: set(),
}


class InvalidTransition(Exception):
    pass


class VerificationIncomplete(Exception):
    pass


def _transition(req: RagIngestionRequest, candidate: DiscoveryCandidate, new_status: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(req.status, set())
    if new_status not in allowed:
        raise InvalidTransition(f"Cannot move RAG request from '{req.status}' to '{new_status}'")
    req.status = new_status
    candidate.rag_status = new_status


def get_or_create_request(db: Session, candidate: DiscoveryCandidate) -> RagIngestionRequest:
    req = db.execute(
        select(RagIngestionRequest).where(RagIngestionRequest.candidate_id == candidate.id)
    ).scalars().first()
    if req is None:
        req = RagIngestionRequest(candidate_id=candidate.id, status=RagStatus.not_selected.value)
        db.add(req)
        db.flush()
    return req


def submit(db: Session, candidate: DiscoveryCandidate, performed_by: str, notes: str | None = None) -> RagIngestionRequest:
    req = get_or_create_request(db, candidate)
    _transition(req, candidate, RagStatus.pending_approval.value)
    write_audit_log(
        db, candidate_id=candidate.id, action=AuditAction.rag_submitted, performed_by=performed_by, notes=notes,
    )
    return req


def verify(
    db: Session,
    candidate: DiscoveryCandidate,
    performed_by: str,
    *,
    source_verified: bool,
    original_source_accessible: bool,
    scientific_relevance_confirmed: bool,
    suitable_for_ask_veda: bool,
    content_permitted_for_ingestion: bool,
    notes: str | None = None,
) -> RagIngestionRequest:
    """
    Record the RAG verification checklist (spec #24).

    Restricted to the `pending_approval` state. Verification is a step
    in the not_selected -> pending_approval -> approved pipeline and must
    NOT be able to silently modify a request that is anywhere else --
    not yet submitted (`not_selected`), rejected and not yet resubmitted
    (`rejected`), or already past approval (`approved`, `queued`,
    `processing`, `indexed`, `failed`, `removed`). Re-verifying a request
    in any of those states would either be meaningless (nothing to verify
    yet) or misleading (the checklist would stop describing the state
    that was actually approved/ingested, undermining the audit trail
    spec #24 exists to support).
    """
    req = get_or_create_request(db, candidate)

    if req.status != RagStatus.pending_approval.value:
        raise InvalidTransition(
            f"Cannot verify a RAG request in status '{req.status}' -- verification is only "
            f"valid while a request is '{RagStatus.pending_approval.value}' (i.e. after submit(), "
            f"before approve()/reject())."
        )

    req.source_verified = source_verified
    req.original_source_accessible = original_source_accessible
    req.scientific_relevance_confirmed = scientific_relevance_confirmed
    req.suitable_for_ask_veda = suitable_for_ask_veda
    req.content_permitted_for_ingestion = content_permitted_for_ingestion
    req.verification_notes = notes
    req.verified_by = performed_by
    req.verified_at = datetime.now(timezone.utc)
    write_audit_log(
        db, candidate_id=candidate.id, action=AuditAction.rag_verified, performed_by=performed_by, notes=notes,
        new_value={
            "source_verified": source_verified,
            "original_source_accessible": original_source_accessible,
            "scientific_relevance_confirmed": scientific_relevance_confirmed,
            "suitable_for_ask_veda": suitable_for_ask_veda,
            "content_permitted_for_ingestion": content_permitted_for_ingestion,
        },
    )
    return req


def approve(db: Session, candidate: DiscoveryCandidate, performed_by: str) -> RagIngestionRequest:
    """Requires administrator authority (enforced by the router's require_admin
    dependency) AND all five verification checks (spec #24), re-checked here.

    Ordering matters: the state-transition check runs FIRST. A request
    that was never submitted (or is already past `pending_approval`)
    should fail with `InvalidTransition` regardless of its verification
    flags -- "you can't approve something that isn't awaiting approval"
    is a more fundamental problem than "you haven't verified it yet", and
    checking verification first would incorrectly report the latter for
    a candidate that was never even submitted (all flags default False)."""
    req = get_or_create_request(db, candidate)

    allowed = ALLOWED_TRANSITIONS.get(req.status, set())
    if RagStatus.approved.value not in allowed:
        raise InvalidTransition(f"Cannot move RAG request from '{req.status}' to '{RagStatus.approved.value}'")

    if not req.all_checks_passed:
        raise VerificationIncomplete(
            "All verification checks (source_verified, original_source_accessible, "
            "scientific_relevance_confirmed, suitable_for_ask_veda, "
            "content_permitted_for_ingestion) must pass before approval."
        )

    _transition(req, candidate, RagStatus.approved.value)
    req.approved_by = performed_by
    req.approved_at = datetime.now(timezone.utc)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.rag_approved, performed_by=performed_by)
    return req


def reject(db: Session, candidate: DiscoveryCandidate, performed_by: str, reason: str) -> RagIngestionRequest:
    req = get_or_create_request(db, candidate)
    _transition(req, candidate, RagStatus.rejected.value)
    req.rejected_by = performed_by
    req.rejected_at = datetime.now(timezone.utc)
    req.rejection_reason = reason
    write_audit_log(
        db, candidate_id=candidate.id, action=AuditAction.rag_rejected, performed_by=performed_by, notes=reason,
    )
    return req


def enqueue(db: Session, candidate: DiscoveryCandidate, req: RagIngestionRequest, metadata: dict, knowledge_version: str) -> RagIngestionRequest:
    _transition(req, candidate, RagStatus.queued.value)
    req.ingestion_metadata = metadata
    req.knowledge_version = knowledge_version
    req.queued_at = datetime.now(timezone.utc)
    db.flush()
    return req


def mark_processing(db: Session, candidate: DiscoveryCandidate, req: RagIngestionRequest) -> RagIngestionRequest:
    _transition(req, candidate, RagStatus.processing.value)
    req.processing_at = datetime.now(timezone.utc)
    req.attempt_count += 1
    db.flush()
    return req


def mark_indexed(db: Session, candidate: DiscoveryCandidate, req: RagIngestionRequest, external_id: str) -> RagIngestionRequest:
    _transition(req, candidate, RagStatus.indexed.value)
    req.indexed_at = datetime.now(timezone.utc)
    req.external_ingestion_id = external_id
    req.last_error = None
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.ingestion_completed, performed_by="worker")
    return req


def mark_failed(db: Session, candidate: DiscoveryCandidate, req: RagIngestionRequest, error: str) -> RagIngestionRequest:
    """Technical failure ONLY -- never used for content/human rejection (spec #51)."""
    _transition(req, candidate, RagStatus.failed.value)
    req.failed_at = datetime.now(timezone.utc)
    req.last_error = error
    write_audit_log(
        db, candidate_id=candidate.id, action=AuditAction.ingestion_failed, performed_by="worker", notes=error,
    )
    return req


def retry(db: Session, candidate: DiscoveryCandidate, req: RagIngestionRequest, performed_by: str) -> RagIngestionRequest:
    _transition(req, candidate, RagStatus.queued.value)
    req.queued_at = datetime.now(timezone.utc)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.rag_submitted, performed_by=performed_by, notes="retry")
    return req


def remove(db: Session, candidate: DiscoveryCandidate, req: RagIngestionRequest, performed_by: str) -> RagIngestionRequest:
    _transition(req, candidate, RagStatus.removed.value)
    req.removed_at = datetime.now(timezone.utc)
    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.removed, performed_by=performed_by)
    return req
