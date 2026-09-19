import uuid

from app.models.enums import RecordDedupeStatus
from app.models.source import DiscoverySource
from app.services.candidate_service import (
    apply_manual_override,
    create_candidate_from_source_record,
    infer_content_type,
    refresh_candidate_from_updated_record,
)
from app.services.deduplication import deduplicate
from app.services.normalization import normalize_to_source_record
from app.schemas.candidate import CandidateOverrideUpdate
from tests.fixtures.golden_dataset import (
    CLINICAL_TRIAL_RECRUITING,
    CLINICAL_TRIAL_UPDATED_STATUS,
    CMT_SPECIFIC_RESEARCH,
)


def _make_source(db, **overrides):
    defaults = dict(
        source_name="Test PubMed",
        source_type="pubmed",
        source_tier="tier_1",
        collection_method="official_api",
        enabled=True,
        frequency="daily",
        configuration={"collector": "pubmed"},
    )
    defaults.update(overrides)
    source = DiscoverySource(**defaults)
    db.add(source)
    db.flush()
    return source


def test_infer_content_type_from_source():
    pubmed_source = DiscoverySource(
        source_name="PubMed", source_type="pubmed", source_tier="tier_1",
        collection_method="official_api", configuration={"collector": "pubmed"},
    )
    ct_source = DiscoverySource(
        source_name="ClinicalTrials.gov", source_type="clinicaltrials", source_tier="tier_1",
        collection_method="official_api", configuration={"collector": "clinicaltrials"},
    )
    other_source = DiscoverySource(
        source_name="Some Org", source_type="rss", source_tier="tier_2",
        collection_method="rss_atom", configuration={},
    )
    assert infer_content_type(pubmed_source) == "research_paper"
    assert infer_content_type(ct_source) == "clinical_trial"
    assert infer_content_type(other_source) == "other"


def test_create_candidate_from_new_source_record(db_session):
    source = _make_source(db_session)
    record = normalize_to_source_record(CMT_SPECIFIC_RESEARCH, source_id=source.id, source_type=source.source_type)
    result = deduplicate(db_session, record)
    assert result.status == RecordDedupeStatus.new

    candidate = create_candidate_from_source_record(db_session, result.record, source)

    assert candidate.title == CMT_SPECIFIC_RESEARCH.title
    assert candidate.doi == CMT_SPECIFIC_RESEARCH.doi
    assert candidate.pmid == CMT_SPECIFIC_RESEARCH.pmid
    assert candidate.content_type == "research_paper"
    assert candidate.source_reliability == "high"  # tier_1 -> high
    assert candidate.has_manual_override is False
    assert result.record.candidate_id == candidate.id


def test_refresh_candidate_on_updated_trial_status(db_session):
    source = _make_source(
        db_session, source_name="Test CT", source_type="clinicaltrials", configuration={"collector": "clinicaltrials"}
    )
    first_record = normalize_to_source_record(CLINICAL_TRIAL_RECRUITING, source_id=source.id, source_type=source.source_type)
    first_result = deduplicate(db_session, first_record)
    candidate = create_candidate_from_source_record(db_session, first_result.record, source)
    original_last_update = candidate.last_source_update

    second_record = normalize_to_source_record(
        CLINICAL_TRIAL_UPDATED_STATUS, source_id=source.id, source_type=source.source_type
    )
    second_result = deduplicate(db_session, second_record)
    assert second_result.status == RecordDedupeStatus.updated

    refresh_candidate_from_updated_record(db_session, candidate, second_result.record)
    assert candidate.last_source_update != original_last_update


def test_manual_override_wins_and_is_audited(db_session):
    source = _make_source(db_session)
    record = normalize_to_source_record(CMT_SPECIFIC_RESEARCH, source_id=source.id, source_type=source.source_type)
    result = deduplicate(db_session, record)
    candidate = create_candidate_from_source_record(db_session, result.record, source)

    # AI proposed (simulated) peripheral_neuropathy; admin overrides to cmt_specific.
    candidate.scope = "peripheral_neuropathy"

    override = CandidateOverrideUpdate(scope="cmt_specific", reason="Clearly CMT1A-specific per abstract")
    apply_manual_override(db_session, candidate, override, performed_by="admin:test")

    assert candidate.scope == "cmt_specific"
    assert candidate.has_manual_override is True

    from app.models.audit import DiscoveryAuditLog
    from sqlalchemy import select

    logs = db_session.execute(
        select(DiscoveryAuditLog).where(DiscoveryAuditLog.candidate_id == candidate.id, DiscoveryAuditLog.action == "override")
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].old_value["scope"] == "peripheral_neuropathy"
    assert logs[0].new_value["scope"] == "cmt_specific"


def test_manual_override_blocks_future_ai_reclassification(db_session):
    from app.models.analysis import DiscoveryAnalysis
    from app.services.candidate_service import apply_analysis_to_candidate

    source = _make_source(db_session)
    record = normalize_to_source_record(CMT_SPECIFIC_RESEARCH, source_id=source.id, source_type=source.source_type)
    result = deduplicate(db_session, record)
    candidate = create_candidate_from_source_record(db_session, result.record, source)

    override = CandidateOverrideUpdate(scope="cmt_specific", reason="human review")
    apply_manual_override(db_session, candidate, override, performed_by="admin:test")

    analysis = DiscoveryAnalysis(
        candidate_id=candidate.id,
        cmt_relevance_score=10,
        peripheral_neuropathy_relevance_score=10,
        clinical_relevance_score=10,
        research_importance_score=10,
        patient_relevance_score=10,
        analysis_confidence=90,
        proposed_scope="general_health_relevance",  # AI disagrees post-override
        proposed_genes=[],
        proposed_cmt_subtypes=[],
        proposed_topics=[],
    )
    db_session.add(analysis)
    db_session.flush()

    apply_analysis_to_candidate(db_session, candidate, analysis)

    # Human decision must win (spec #29): scope stays cmt_specific.
    assert candidate.scope == "cmt_specific"
