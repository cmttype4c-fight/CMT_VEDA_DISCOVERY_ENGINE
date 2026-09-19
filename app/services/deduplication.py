"""
Deduplication service (spec #11-#12).

Deterministic matching order against existing *current* source records,
regardless of which source collected them (the same paper/trial can be
picked up by more than one source):

    DOI -> PMID -> ClinicalTrials.gov ID -> canonical URL ->
    normalized title + author/institution/date

Outcomes:
  NEW        -- no match found; record is genuinely new.
  DUPLICATE  -- match found and content is unchanged (same content_hash).
                No new row is written; only `last_seen_at` is touched.
  UPDATED    -- match found but content differs (e.g. a trial's status
                changed). The previous record is marked not-current and a
                new record row is inserted and linked via
                `supersedes_record_id`, preserving history (spec #12)
                rather than overwriting.

Idempotency (spec #49): running the same source collection twice is safe
-- the second run sees its own first-run output as the "existing" record
and classifies everything as DUPLICATE (or UPDATED only if something
genuinely changed upstream), never creating duplicate candidates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.base import normalize_text
from app.models.enums import RecordDedupeStatus
from app.models.source_record import DiscoverySourceRecord


@dataclass
class DedupeResult:
    status: RecordDedupeStatus
    record: DiscoverySourceRecord
    matched_existing: DiscoverySourceRecord | None = None


def _find_existing(db: Session, incoming: DiscoverySourceRecord) -> DiscoverySourceRecord | None:
    base = select(DiscoverySourceRecord).where(DiscoverySourceRecord.is_current.is_(True))

    if incoming.doi:
        match = db.execute(base.where(DiscoverySourceRecord.doi == incoming.doi)).scalars().first()
        if match:
            return match

    if incoming.pmid:
        match = db.execute(base.where(DiscoverySourceRecord.pmid == incoming.pmid)).scalars().first()
        if match:
            return match

    if incoming.clinical_trial_id:
        match = db.execute(
            base.where(DiscoverySourceRecord.clinical_trial_id == incoming.clinical_trial_id)
        ).scalars().first()
        if match:
            return match

    if incoming.canonical_url:
        match = db.execute(
            base.where(DiscoverySourceRecord.canonical_url == incoming.canonical_url)
        ).scalars().first()
        if match:
            return match

    # Fallback: normalized title + institution/journal + publication_date.
    # Done in Python (not SQL) since normalization involves regex cleanup
    # that isn't portable across SQLite/Postgres.
    incoming_title = normalize_text(incoming.title)
    if incoming_title:
        candidates = db.execute(base.where(DiscoverySourceRecord.title.isnot(None))).scalars().all()
        for candidate in candidates:
            if normalize_text(candidate.title) != incoming_title:
                continue
            same_date = candidate.publication_date == incoming.publication_date
            same_org = (candidate.institution or candidate.journal or "") == (
                incoming.institution or incoming.journal or ""
            )
            if same_date or same_org:
                return candidate

    return None


def deduplicate(db: Session, incoming: DiscoverySourceRecord) -> DedupeResult:
    existing = _find_existing(db, incoming)

    if existing is None:
        incoming.dedupe_status = RecordDedupeStatus.new.value
        db.add(incoming)
        db.flush()
        return DedupeResult(status=RecordDedupeStatus.new, record=incoming)

    if existing.content_hash == incoming.content_hash:
        existing.last_seen_at = datetime.now(timezone.utc)
        db.flush()
        return DedupeResult(status=RecordDedupeStatus.duplicate, record=existing, matched_existing=existing)

    # Content changed -> UPDATED: supersede, preserve history (spec #12).
    existing.is_current = False
    incoming.supersedes_record_id = existing.id
    incoming.candidate_id = existing.candidate_id
    incoming.dedupe_status = RecordDedupeStatus.updated.value
    db.add(incoming)
    db.flush()
    return DedupeResult(status=RecordDedupeStatus.updated, record=incoming, matched_existing=existing)
