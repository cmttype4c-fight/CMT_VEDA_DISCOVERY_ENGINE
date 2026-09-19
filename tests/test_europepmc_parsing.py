"""
CMT-specific overhaul, Phase 3A: Europe PMC collector, unit-tested against
fixture JSON (no network access -- see app/collectors/europepmc.py's
confidence caveat).
"""
import respx
import httpx

from app.collectors.europepmc import EuropePMCCollector, build_query, parse_europepmc_result
from app.models.source import DiscoverySource
from tests.fixtures.europepmc_responses import SEARCH_RESPONSE_EMPTY, SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT


def test_parse_europepmc_result_extracts_core_fields():
    item = SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT["resultList"]["result"][0]
    record = parse_europepmc_result(item)
    assert record is not None
    assert record.external_id == "38111111"
    assert record.pmid == "38111111"
    assert record.doi == "10.1000/cmt-europepmc-example"
    assert "Charcot-Marie-Tooth" in record.title
    assert record.authors == ["A. Researcher", "B. Scientist"]
    assert record.raw_metadata["is_open_access"] is True
    assert len(record.raw_metadata["full_text_urls"]) == 2


def test_parse_europepmc_result_returns_none_without_any_id():
    assert parse_europepmc_result({"title": "No id here"}) is None


def test_build_query_uses_disease_terms_only_by_default():
    from app.collectors.europepmc import DEFAULT_QUERY_TERMS

    query = build_query(DEFAULT_QUERY_TERMS)
    assert '"Charcot-Marie-Tooth"' in query
    assert "MFN2" not in query  # genes are not part of default retrieval terms


def _source(**config):
    return DiscoverySource(
        source_name="Test Europe PMC", source_type="europepmc", source_tier="tier_1",
        collection_method="official_api", configuration={"collector": "europepmc", **config},
    )


@respx.mock
async def test_collector_yields_records_from_search_response():
    respx.route(method="GET", host="www.ebi.ac.uk").mock(
        return_value=httpx.Response(200, json=SEARCH_RESPONSE_ONE_OPEN_ACCESS_RESULT)
    )
    collector = EuropePMCCollector(_source())
    results = [r async for r in collector.collect()]
    assert len(results) == 1
    assert results[0].pmid == "38111111"


@respx.mock
async def test_collector_handles_empty_results():
    respx.route(method="GET", host="www.ebi.ac.uk").mock(return_value=httpx.Response(200, json=SEARCH_RESPONSE_EMPTY))
    collector = EuropePMCCollector(_source())
    results = [r async for r in collector.collect()]
    assert results == []


@respx.mock
async def test_collector_stops_when_next_cursor_mark_repeats():
    """Guards against an infinite loop if the API ever returns the same
    cursorMark twice (defensive -- matches the same care this codebase
    already applies to ClinicalTrials.gov pagination)."""
    respx.route(method="GET", host="www.ebi.ac.uk").mock(return_value=httpx.Response(200, json=SEARCH_RESPONSE_EMPTY))
    collector = EuropePMCCollector(_source())
    results = [r async for r in collector.collect()]
    assert results == []
