"""
Candidate service (spec #14, #29).

Owns the lifecycle of the central `DiscoveryCandidate` object:
  - creation from a NEW source record
  - refresh from an UPDATED source record (e.g. trial status change)
  - applying Veda Intelligence's proposed classification
  - applying an administrator's manual override

Manual-override precedence (spec #29): once `has_manual_override` is set,
`apply_analysis_to_candidate` will NOT overwrite the candidate's
classification fields from a newer AI analysis pass -- the human decision
wins. (This is a candidate-level flag rather than per-field; a field-level
override map is a reasonable future enhancement if editors need to accept
some AI-proposed fields while overriding others, but was out of scope for
v1 given the spec's emphasis on correctness over UI nuance.)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.analysis import DiscoveryAnalysis
from app.models.candidate import DiscoveryCandidate
from app.models.enums import AuditAction, ContentType, SourceReliability
from app.models.source import DiscoverySource
from app.models.source_record import DiscoverySourceRecord
from app.schemas.candidate import CandidateOverrideUpdate
from app.services.audit_service import write_audit_log

_CONTENT_TYPE_BY_SOURCE_TYPE = {
    "clinicaltrials": ContentType.clinical_trial.value,
    "pubmed": ContentType.research_paper.value,
}

# Starting-point reliability signal derived from source tier (spec #6:
# tier is a governance/reliability signal, NOT automatically scientific
# truth). This is only ever the *initial* value -- Veda Intelligence and
# human reviewers can and should refine it per candidate.
_RELIABILITY_BY_TIER = {
    "tier_1": SourceReliability.high.value,
    "tier_2": SourceReliability.high.value,
    "tier_3": SourceReliability.moderate.value,
    "tier_4": SourceReliability.moderate.value,
    "tier_5": SourceReliability.limited.value,
    "tier_6": SourceReliability.limited.value,
}


def infer_content_type(source: DiscoverySource) -> str:
    key = (source.configuration or {}).get("collector") or source.source_type
    return _CONTENT_TYPE_BY_SOURCE_TYPE.get(key, ContentType.other.value)


def create_candidate_from_source_record(
    db: Session, source_record: DiscoverySourceRecord, source: DiscoverySource
) -> DiscoveryCandidate:
    now = datetime.now(timezone.utc)
    candidate = DiscoveryCandidate(
        content_type=infer_content_type(source),
        title=source_record.title or "(untitled)",
        language="en",
        discovered_at=now,
        original_date=source_record.publication_date,
        last_source_update=now,
        source_id=source.id,
        source_name=source.source_name,
        source_type=source.source_type,
        source_url=source_record.canonical_url,
        source_tier=source.source_tier,
        authors=source_record.authors or [],
        institution=source_record.institution,
        journal=source_record.journal,
        doi=source_record.doi,
        pmid=source_record.pmid,
        clinical_trial_id=source_record.clinical_trial_id,
        publisher=source_record.publisher,
        source_reliability=_RELIABILITY_BY_TIER.get(source.source_tier, SourceReliability.unknown.value),
        abstract=source_record.abstract or source_record.description,
    )
    db.add(candidate)
    db.flush()

    source_record.candidate_id = candidate.id

    write_audit_log(
        db,
        candidate_id=candidate.id,
        action=AuditAction.discovered,
        performed_by=f"collector:{source.source_name}",
        new_value={"source_record_id": str(source_record.id), "title": candidate.title},
    )
    return candidate


def refresh_candidate_from_updated_record(
    db: Session, candidate: DiscoveryCandidate, source_record: DiscoverySourceRecord
) -> DiscoveryCandidate:
    """Applied when dedupe classifies an incoming record as UPDATED."""
    old_status = None
    if isinstance(source_record.raw_metadata, dict):
        old_status = source_record.raw_metadata.get("status")

    candidate.last_source_update = datetime.now(timezone.utc)
    if source_record.title:
        candidate.title = source_record.title
    if source_record.abstract or source_record.description:
        candidate.abstract = source_record.abstract or source_record.description

    write_audit_log(
        db,
        candidate_id=candidate.id,
        action=AuditAction.discovered,
        performed_by=f"collector:{candidate.source_name}",
        old_value={"note": "source record updated"},
        new_value={"source_record_id": str(source_record.id), "status": old_status},
        notes="Source record changed upstream (e.g. trial status transition); candidate refreshed, "
        "history preserved on discovery_source_records.",
    )
    return candidate


def apply_analysis_to_candidate(
    db: Session, candidate: DiscoveryCandidate, analysis: DiscoveryAnalysis
) -> DiscoveryCandidate:
    if candidate.has_manual_override:
        write_audit_log(
            db,
            candidate_id=candidate.id,
            action=AuditAction.classified,
            performed_by="veda_intelligence",
            notes="New analysis generated but NOT applied to candidate classification: "
            "candidate has an active manual override (spec #29 -- human decision wins).",
        )
        return candidate

    old_value = {
        "scope": candidate.scope,
        "cmt_subtypes": candidate.cmt_subtypes,
        "genes": candidate.genes,
        "topics": candidate.topics,
    }

    candidate.scope = analysis.proposed_scope or candidate.scope
    candidate.cmt_subtypes = analysis.proposed_cmt_subtypes or candidate.cmt_subtypes
    candidate.genes = analysis.proposed_genes or candidate.genes
    candidate.topics = analysis.proposed_topics or candidate.topics

    write_audit_log(
        db,
        candidate_id=candidate.id,
        action=AuditAction.classified,
        performed_by="veda_intelligence",
        old_value=old_value,
        new_value={
            "scope": candidate.scope,
            "cmt_subtypes": candidate.cmt_subtypes,
            "genes": candidate.genes,
            "topics": candidate.topics,
        },
        notes=f"analysis_id={analysis.id}",
    )
    return candidate


def apply_manual_override(
    db: Session,
    candidate: DiscoveryCandidate,
    override: CandidateOverrideUpdate,
    performed_by: str,
) -> DiscoveryCandidate:
    update_fields = override.model_dump(exclude_unset=True, exclude={"reason"})
    old_value: dict[str, Any] = {}
    new_value: dict[str, Any] = {}

    for field_name, new_val in update_fields.items():
        old_val = getattr(candidate, field_name)
        old_serialized = old_val.value if hasattr(old_val, "value") else old_val
        new_serialized = new_val.value if hasattr(new_val, "value") else new_val
        if old_serialized == new_serialized:
            continue
        old_value[field_name] = old_serialized
        new_value[field_name] = new_serialized
        setattr(candidate, field_name, new_serialized)

    if not new_value:
        return candidate

    candidate.has_manual_override = True

    write_audit_log(
        db,
        candidate_id=candidate.id,
        action=AuditAction.override,
        performed_by=performed_by,
        old_value=old_value,
        new_value=new_value,
        notes=override.reason,
    )
    return candidate
