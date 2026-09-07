import uuid

from app.models.enums import RecordDedupeStatus
from app.services.deduplication import deduplicate
from app.services.normalization import normalize_to_source_record
from tests.fixtures.golden_dataset import (
    CLINICAL_TRIAL_RECRUITING,
    CLINICAL_TRIAL_UPDATED_STATUS,
    CMT_SPECIFIC_RESEARCH,
    DUPLICATE_OF_CMT_SPECIFIC,
)

SOURCE_ID = uuid.uuid4()


def _rec(normalized):
    return normalize_to_source_record(normalized, source_id=SOURCE_ID, source_type="pubmed")


def test_new_record_has_no_match(db_session):
    result = deduplicate(db_session, _rec(CMT_SPECIFIC_RESEARCH))
    assert result.status == RecordDedupeStatus.new
    assert result.matched_existing is None


def test_exact_duplicate_by_doi_and_pmid(db_session):
    deduplicate(db_session, _rec(CMT_SPECIFIC_RESEARCH))
    result = deduplicate(db_session, _rec(DUPLICATE_OF_CMT_SPECIFIC))
    assert result.status == RecordDedupeStatus.duplicate


def test_clinical_trial_status_change_is_updated_not_duplicate(db_session):
    first = deduplicate(db_session, _rec(CLINICAL_TRIAL_RECRUITING))
    assert first.status == RecordDedupeStatus.new

    second = deduplicate(db_session, _rec(CLINICAL_TRIAL_UPDATED_STATUS))
    assert second.status == RecordDedupeStatus.updated
    assert second.matched_existing.id == first.record.id
    # History preserved: the old record is no longer "current", not deleted.
    assert first.record.is_current is False
    assert second.record.is_current is True
    assert second.record.supersedes_record_id == first.record.id


def test_dedupe_by_url_when_no_doi_pmid_ctid(db_session):
    from app.collectors.base import NormalizedRecord

    a = NormalizedRecord(external_id="a1", canonical_url="https://example.org/paper", title="Some paper title")
    b = NormalizedRecord(external_id="a2", canonical_url="https://example.org/paper", title="Some paper title")

    first = deduplicate(db_session, _rec(a))
    second = deduplicate(db_session, _rec(b))
    assert first.status == RecordDedupeStatus.new
    assert second.status == RecordDedupeStatus.duplicate


def test_dedupe_by_normalized_title_fallback(db_session):
    from datetime import date

    from app.collectors.base import NormalizedRecord

    a = NormalizedRecord(
        external_id="t1", title="A Study of Nerve Conduction!", institution="Example U", publication_date=date(2025, 1, 1)
    )
    b = NormalizedRecord(
        external_id="t2", title="a study of nerve conduction", institution="Example U", publication_date=date(2025, 1, 1)
    )

    first = deduplicate(db_session, _rec(a))
    second = deduplicate(db_session, _rec(b))
    assert first.status == RecordDedupeStatus.new
    assert second.status == RecordDedupeStatus.duplicate


def test_running_same_collection_twice_does_not_duplicate_candidates(db_session):
    """Idempotency (spec #49): collecting the exact same record twice must
    never look like two different new records."""
    first = deduplicate(db_session, _rec(CMT_SPECIFIC_RESEARCH))
    second = deduplicate(db_session, _rec(CMT_SPECIFIC_RESEARCH))
    assert first.status == RecordDedupeStatus.new
    assert second.status == RecordDedupeStatus.duplicate
