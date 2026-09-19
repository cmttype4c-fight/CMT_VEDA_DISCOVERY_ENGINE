"""
CMT-specific overhaul, Phase 2A/2B: retrieval tightening at the collector
level (distinct from tests/test_eligibility_gate.py, which covers the
downstream gate -- these tests cover the earlier point where the
*retrieval query itself* is constructed).
"""
from app.collectors.clinicaltrials import parse_clinicaltrials_study
from app.collectors.pubmed import DEFAULT_VOCABULARY, build_query


def test_pubmed_query_does_not_include_gene_terms():
    """Test list item 1 (CMT-specific PubMed query construction) +
    the core Phase 2A requirement: gene-only retrieval must not dominate
    the candidate pool -- genes are no longer part of the retrieval query
    at all."""
    query = build_query(DEFAULT_VOCABULARY)
    for gene in DEFAULT_VOCABULARY["genetics"]:
        assert f'"{gene}"[tiab]' not in query, f"gene term {gene!r} should not appear in the retrieval query"


def test_pubmed_query_includes_disease_and_subtype_terms():
    query = build_query(DEFAULT_VOCABULARY)
    assert '"Charcot-Marie-Tooth"[tiab]' in query
    assert '"CMT1A"[tiab]' in query  # from the new cmt_subtype group


def test_pubmed_query_falls_back_to_disease_only_vocabulary_shape():
    """Backward compatible with a vocabulary dict that only has a
    "disease" key (the shape used elsewhere in this codebase/tests)."""
    vocab = {"disease": ["Charcot-Marie-Tooth", "CMT"]}
    query = build_query(vocab)
    assert '"Charcot-Marie-Tooth"[tiab]' in query
    assert '"CMT"[tiab]' in query


def test_pubmed_query_override_still_bypasses_all_of_this():
    """configuration["query_override"] (handled in PubMedCollector.collect,
    not build_query) remains the escape hatch for gene-inclusive retrieval
    when deliberately wanted -- build_query itself is only ever consulted
    when no override is set."""
    vocab = {"genetics": ["MFN2"]}  # no "disease" key at all
    query = build_query(vocab)
    assert query == ""  # confirms genetics alone produces no retrieval clause


def test_clinicaltrials_parses_structured_conditions():
    """Test list item 5: ClinicalTrials structured condition is
    considered -- confirms the parser actually captures
    conditionsModule.conditions into raw_metadata."""
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT12345678", "officialTitle": "A Study"},
            "statusModule": {"overallStatus": "RECRUITING"},
            "conditionsModule": {"conditions": ["Charcot-Marie-Tooth Disease Type 1A", "Peripheral Neuropathy"]},
        }
    }
    record = parse_clinicaltrials_study(study)
    assert record is not None
    assert record.raw_metadata["conditions"] == ["Charcot-Marie-Tooth Disease Type 1A", "Peripheral Neuropathy"]


def test_clinicaltrials_parses_empty_conditions_module_safely():
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT99999999", "officialTitle": "A Study"},
            "statusModule": {"overallStatus": "RECRUITING"},
        }
    }
    record = parse_clinicaltrials_study(study)
    assert record is not None
    assert record.raw_metadata["conditions"] == []
