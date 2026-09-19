"""
Locks in the deliberate v1 behavior documented in
app/collectors/clinicaltrials.py: `since` is accepted (it's part of the
shared BaseCollector interface) but does NOT currently narrow or sort the
ClinicalTrials.gov query. This is a considered safety choice, not an
oversight -- see that module's docstring and IMPLEMENTATION_STATUS.md for
the full reasoning (a wrong assumption about the API's sort/filter
behavior could silently skip genuinely updated trials, which is worse
than the current inefficiency of re-fetching unchanged ones).

This test exists so that if incremental filtering is implemented later,
it happens as a conscious, tested change -- not a silent behavior shift
that nobody noticed.

The route matcher below intentionally matches on HOST only (not the full
path), so this test's correctness depends only on what it's actually
checking -- the query parameters -- and not on incidental httpx
base_url/path-joining behavior this build has no way to execute and
confirm.
"""
from datetime import datetime, timezone

import httpx
import respx

from app.collectors.clinicaltrials import ClinicalTrialsCollector
from app.models.source import DiscoverySource


def _source(**config_overrides):
    config = {"collector": "clinicaltrials"}
    config.update(config_overrides)
    return DiscoverySource(
        source_name="Test ClinicalTrials.gov",
        source_type="clinicaltrials",
        source_tier="tier_1",
        collection_method="official_api",
        base_url="https://clinicaltrials.gov/api/v2",
        configuration=config,
    )


def _empty_page(request):
    return httpx.Response(200, json={"studies": [], "nextPageToken": None})


@respx.mock
async def test_since_does_not_add_any_filter_or_sort_params():
    captured_requests = []

    def _capture(request):
        captured_requests.append(request)
        return _empty_page(request)

    respx.route(method="GET", host="clinicaltrials.gov").mock(side_effect=_capture)

    collector = ClinicalTrialsCollector(_source())
    since = datetime(2025, 1, 1, tzinfo=timezone.utc)

    results = [record async for record in collector.collect(since=since)]

    assert results == []
    assert len(captured_requests) == 1
    params = dict(captured_requests[0].url.params)

    # None of these would be present if a date/sort filter were added --
    # if this assertion ever fails, it should be because someone
    # deliberately implemented and tested incremental filtering, not
    # because of an accidental change.
    assert "sort" not in params
    assert "filter.lastUpdatePostDate" not in params
    assert not any("lastupdate" in str(v).lower() for v in params.values())
    assert not any("lastupdate" in str(k).lower() for k in params.keys())


@respx.mock
async def test_since_none_and_since_set_send_identical_request_params():
    """Extra confirmation that `since=None` and a real `since` value
    produce byte-identical requests -- i.e. `since` truly has zero
    effect on the query today, in either direction."""
    captured_requests = []

    def _capture(request):
        captured_requests.append(request)
        return _empty_page(request)

    respx.route(method="GET", host="clinicaltrials.gov").mock(side_effect=_capture)

    collector_a = ClinicalTrialsCollector(_source())
    _ = [record async for record in collector_a.collect(since=None)]

    collector_b = ClinicalTrialsCollector(_source())
    _ = [record async for record in collector_b.collect(since=datetime(2025, 1, 1, tzinfo=timezone.utc))]

    assert len(captured_requests) == 2
    params_a = dict(captured_requests[0].url.params)
    params_b = dict(captured_requests[1].url.params)
    assert params_a == params_b

