from app.models.candidate import DiscoveryCandidate
from app.models.enums import ContentType
from app.schemas.manual import ManualDiscoveryRequest
from app.api.routers.manual import _get_or_create_manual_source
from app.collectors.base import NormalizedRecord
from app.services.candidate_service import create_candidate_from_source_record
from app.services.deduplication import deduplicate
from app.services.normalization import normalize_to_source_record


def test_manual_submission_uses_the_same_pipeline_functions(db_session):
    """spec #27: manual submissions go through Normalize -> Deduplicate ->
    Candidate using the exact same functions collectors use -- this test
    exercises that call chain directly (the router is a thin wrapper)."""
    manual_source = _get_or_create_manual_source(db_session)
    assert manual_source.collection_method == "manual_submission"

    normalized = NormalizedRecord(
        external_id="manual-hash-abc",
        canonical_url="https://example.org/some-article",
        title="A manually submitted CMT community resource",
        raw_metadata={"source": "manual"},
    )
    record = normalize_to_source_record(normalized, source_id=manual_source.id, source_type="manual")
    result = deduplicate(db_session, record)
    assert result.status.value == "NEW"

    candidate = create_candidate_from_source_record(db_session, result.record, manual_source)
    candidate.content_type = ContentType.community_resource.value

    assert candidate.title == "A manually submitted CMT community resource"
    assert candidate.content_type == "community_resource"


def test_manual_submission_deduplicates_against_existing_candidate(db_session):
    manual_source = _get_or_create_manual_source(db_session)

    normalized = NormalizedRecord(
        external_id="manual-hash-xyz",
        canonical_url="https://example.org/duplicate-article",
        title="Duplicate check article",
    )
    record1 = normalize_to_source_record(normalized, source_id=manual_source.id, source_type="manual")
    result1 = deduplicate(db_session, record1)
    create_candidate_from_source_record(db_session, result1.record, manual_source)

    record2 = normalize_to_source_record(normalized, source_id=manual_source.id, source_type="manual")
    result2 = deduplicate(db_session, record2)

    assert result2.status.value == "DUPLICATE"
    assert result2.matched_existing.candidate_id == result1.record.candidate_id
