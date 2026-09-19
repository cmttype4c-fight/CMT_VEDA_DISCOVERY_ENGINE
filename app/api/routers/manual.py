"""
Manual discovery API (spec #27).

Runs through the EXACT SAME pipeline as any collector: Normalize ->
Deduplicate -> Candidate. There is no separate manual code path -- this
endpoint builds one `NormalizedRecord` and hands it to the same
`normalize_to_source_record` / `deduplicate` / `create_candidate_from_source_record`
functions the worker uses for PubMed/ClinicalTrials/RSS.

DELIBERATE DECISION (CMT-specific overhaul, Phase 2C): the automated CMT
eligibility gate (app/services/intelligence/eligibility.py) is NOT applied
here. That gate exists to keep an *automated, high-volume* collector run
from flooding the candidate queue with off-topic material a human never
looked at. A manual submission is the opposite case: a
reviewer-or-admin-authenticated human (require_reviewer_or_admin below)
has already made the CMT-relevance judgment themselves by choosing to
submit this specific item -- the same "a human decision wins" principle
already applied elsewhere in this engine to `has_manual_override`
(app/services/candidate_service.py). Auto-rejecting a human's deliberate
submission because it didn't match a keyword list would be a worse
outcome than the noise problem this whole overhaul exists to fix.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth import Principal, require_reviewer_or_admin
from app.collectors.base import NormalizedRecord
from app.models.enums import RecordDedupeStatus
from app.models.source import DiscoverySource
from app.schemas.manual import ManualDiscoveryRequest, ManualDiscoveryResponse
from app.services.candidate_service import create_candidate_from_source_record
from app.services.deduplication import deduplicate
from app.services.normalization import normalize_to_source_record

router = APIRouter(prefix="/manual-discovery", tags=["manual"])

_MANUAL_SOURCE_NAME = "Manual Submission"


def _get_or_create_manual_source(db: Session) -> DiscoverySource:
    source = db.execute(
        select(DiscoverySource).where(DiscoverySource.source_name == _MANUAL_SOURCE_NAME)
    ).scalars().first()
    if source is None:
        source = DiscoverySource(
            source_name=_MANUAL_SOURCE_NAME,
            source_type="manual",
            source_tier="tier_6",
            collection_method="manual_submission",
            enabled=True,
            frequency="n/a",
            configuration={},
        )
        db.add(source)
        db.flush()
    return source


@router.post("", response_model=ManualDiscoveryResponse, status_code=201)
def create_manual_discovery(
    payload: ManualDiscoveryRequest,
    db: Session = Depends(get_db),
    principal: Principal = Depends(require_reviewer_or_admin),
):
    manual_source = _get_or_create_manual_source(db)

    external_id = hashlib.sha256(payload.url.encode("utf-8")).hexdigest()
    normalized = NormalizedRecord(
        external_id=external_id,
        canonical_url=payload.url,
        title=payload.title,
        raw_metadata={
            "source": "manual",
            "submitted_by": principal.subject,
            "content_type_hint": payload.content_type.value,
            "notes": payload.notes,
            "source_name_label": payload.source_name,
        },
    )

    source_record = normalize_to_source_record(
        normalized, source_id=manual_source.id, source_type="manual"
    )
    result = deduplicate(db, source_record)

    candidate_id = None
    if result.status == RecordDedupeStatus.new:
        candidate = create_candidate_from_source_record(db, result.record, manual_source)
        candidate.content_type = payload.content_type.value
        candidate.source_name = payload.source_name
        candidate_id = candidate.id
    elif result.matched_existing:
        candidate_id = result.matched_existing.candidate_id

    db.commit()

    return ManualDiscoveryResponse(
        source_record_id=result.record.id, candidate_id=candidate_id, dedupe_status=result.status.value
    )
