"""Collector parsing tests (spec #45) -- pure functions, no network."""
from app.collectors.clinicaltrials import parse_clinicaltrials_study
from app.collectors.pubmed import parse_pubmed_xml
from tests.fixtures.clinicaltrials_responses import SAMPLE_STUDIES_RESPONSE, SAMPLE_STUDY_MISSING_NCT_ID
from tests.fixtures.pubmed_responses import SAMPLE_EFETCH_XML


def test_parse_pubmed_xml_extracts_core_fields():
    records = parse_pubmed_xml(SAMPLE_EFETCH_XML)
    assert len(records) == 1
    record = records[0]
    assert record.pmid == "10000099"
    assert record.external_id == "10000099"
    assert "PMP22 dosage sensitivity" in record.title
    assert record.doi == "10.1000/pmp22-mouse-model"
    assert record.authors == ["Jane Smith", "John Doe"]
    assert record.publication_date.year == 2025
    assert record.publication_date.month == 6
    assert record.canonical_url.endswith("10000099/")


def test_parse_clinicaltrials_study_extracts_core_fields():
    study = SAMPLE_STUDIES_RESPONSE["studies"][0]
    record = parse_clinicaltrials_study(study)
    assert record is not None
    assert record.clinical_trial_id == "NCT90000099"
    assert record.external_id == "NCT90000099"
    assert record.raw_metadata["status"] == "RECRUITING"
    assert record.institution == "Example University Medical Center"
    assert record.publisher == "Example Neurology Research Institute"
    assert record.publication_date.isoformat() == "2025-02-01"


def test_parse_clinicaltrials_study_returns_none_without_nct_id():
    assert parse_clinicaltrials_study(SAMPLE_STUDY_MISSING_NCT_ID) is None
