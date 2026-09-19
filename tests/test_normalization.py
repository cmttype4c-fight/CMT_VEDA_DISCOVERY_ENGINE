import uuid

from app.services.normalization import _clean_doi, normalize_to_source_record
from tests.fixtures.golden_dataset import CMT_SPECIFIC_RESEARCH


def test_normalize_to_source_record_maps_fields():
    source_id = uuid.uuid4()
    record = normalize_to_source_record(CMT_SPECIFIC_RESEARCH, source_id=source_id, source_type="pubmed")

    assert record.external_id == "pmid-10000001"
    assert record.source_id == source_id
    assert record.source_type == "pubmed"
    assert record.doi == "10.1000/cmt1a-natural-history"
    assert record.pmid == "10000001"
    assert record.is_current is True
    assert record.content_hash  # non-empty
    assert record.first_seen_at == record.last_seen_at


def test_clean_doi_strips_common_prefixes():
    assert _clean_doi("https://doi.org/10.1000/abc") == "10.1000/abc"
    assert _clean_doi("doi:10.1000/abc") == "10.1000/abc"
    assert _clean_doi("10.1000/abc") == "10.1000/abc"
    assert _clean_doi(None) is None
    assert _clean_doi("  ") is None


def test_content_hash_changes_when_status_changes():
    from tests.fixtures.golden_dataset import CLINICAL_TRIAL_RECRUITING, CLINICAL_TRIAL_UPDATED_STATUS

    assert CLINICAL_TRIAL_RECRUITING.content_hash() != CLINICAL_TRIAL_UPDATED_STATUS.content_hash()


def test_content_hash_stable_for_identical_input():
    from tests.fixtures.golden_dataset import CMT_SPECIFIC_RESEARCH, DUPLICATE_OF_CMT_SPECIFIC

    assert CMT_SPECIFIC_RESEARCH.content_hash() == DUPLICATE_OF_CMT_SPECIFIC.content_hash()
