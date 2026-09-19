"""
FINAL FOCUSED CORRECTION, TASK 5: ClinVar collector, unit-tested against
fixture JSON built to match the real esummary response shape (LIVE-FETCHED
this pass -- see app/collectors/clinvar.py's module docstring). No network
access; respx intercepts the collector's own httpx calls for the
`collect()` integration-shaped tests.
"""
import httpx
import respx

from app.collectors.clinvar import ClinVarCollector, build_query, parse_clinvar_summary
from app.models.source import DiscoverySource
from tests.fixtures.clinvar_responses import (
    ESEARCH_RESPONSE_EMPTY,
    ESEARCH_RESPONSE_TWO_IDS,
    ESUMMARY_RESPONSE_EMPTY,
    ESUMMARY_RESPONSE_ONE_PATHOGENIC_VARIANT,
    ESUMMARY_RESPONSE_UNRELATED_SOMATIC_VARIANT,
)


def test_parse_clinvar_summary_extracts_core_fields_and_conditions():
    records = parse_clinvar_summary(ESUMMARY_RESPONSE_ONE_PATHOGENIC_VARIANT)
    assert len(records) == 1
    record = records[0]
    assert record.external_id == "VCV000077001.3"
    assert "PMP22" in record.title
    assert record.raw_metadata["genes"] == ["PMP22"]
    assert record.raw_metadata["clinical_significance"] == "Pathogenic"
    # Same key/shape ClinicalTrials.gov's collector already populates --
    # this is what plugs a ClinVar record into eligibility.py's existing
    # structured-conditions-first logic with zero changes there.
    assert record.raw_metadata["conditions"] == ["Charcot-Marie-Tooth disease type 1A"]
    assert record.publication_date is not None
    assert record.publication_date.isoformat() == "2025-11-02"
    assert "Pathogenic" in record.description
    assert "Charcot-Marie-Tooth disease type 1A" in record.description


def test_parse_clinvar_summary_falls_back_gracefully_without_germline_classification():
    """A somatic-only record (no `germline_classification` key at all)
    must not raise -- it should still produce a record with an empty
    conditions list rather than aborting the whole batch."""
    records = parse_clinvar_summary(ESUMMARY_RESPONSE_UNRELATED_SOMATIC_VARIANT)
    assert len(records) == 1
    record = records[0]
    assert record.raw_metadata["conditions"] == []
    assert record.raw_metadata["clinical_significance"] is None
    assert "not provided" in record.description


def test_parse_clinvar_summary_handles_empty_result():
    assert parse_clinvar_summary(ESUMMARY_RESPONSE_EMPTY) == []


def test_build_query_uses_disease_terms_with_dis_field_tag():
    from app.collectors.clinvar import DEFAULT_DISEASE_TERMS

    query = build_query(DEFAULT_DISEASE_TERMS)
    assert '"Charcot-Marie-Tooth"[dis]' in query
    assert "PMP22" not in query  # genes are not part of default retrieval terms


def _source(**config):
    return DiscoverySource(
        source_name="Test ClinVar",
        source_type="clinvar",
        source_tier="tier_1",
        collection_method="official_api",
        configuration={"collector": "clinvar", **config},
    )


@respx.mock
async def test_collector_yields_records_from_esearch_then_esummary():
    respx.route(method="GET", host="eutils.ncbi.nlm.nih.gov", path__regex=r".*esearch\.fcgi$").mock(
        return_value=httpx.Response(200, json=ESEARCH_RESPONSE_TWO_IDS)
    )
    respx.route(method="GET", host="eutils.ncbi.nlm.nih.gov", path__regex=r".*esummary\.fcgi$").mock(
        return_value=httpx.Response(
            200,
            json={
                "header": {"type": "esummary", "version": "0.3"},
                "result": {
                    "uids": ["77000001", "77000002"],
                    **ESUMMARY_RESPONSE_ONE_PATHOGENIC_VARIANT["result"],
                    **ESUMMARY_RESPONSE_UNRELATED_SOMATIC_VARIANT["result"],
                },
            },
        )
    )
    collector = ClinVarCollector(_source())
    results = [r async for r in collector.collect()]
    assert len(results) == 2
    assert {r.external_id for r in results} == {"VCV000077001.3", "VCV000077002.1"}


@respx.mock
async def test_collector_handles_empty_esearch_results():
    respx.route(method="GET", host="eutils.ncbi.nlm.nih.gov", path__regex=r".*esearch\.fcgi$").mock(
        return_value=httpx.Response(200, json=ESEARCH_RESPONSE_EMPTY)
    )
    collector = ClinVarCollector(_source())
    results = [r async for r in collector.collect()]
    assert results == []
