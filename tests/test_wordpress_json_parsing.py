"""
FINAL FOCUSED CORRECTION, TASK 5: WordPress REST API collector (CMTRF,
ECMTF, CMT Australia, Peripheral Nerve Society), unit-tested against
fixture JSON built to match the real `/wp-json/wp/v2/posts` response
shape (LIVE-FETCHED this pass -- see app/collectors/wordpress_json.py's
module docstring). No network access; respx intercepts the collector's
own httpx calls for the `collect()` integration-shaped tests.
"""
import httpx
import respx

from app.collectors.wordpress_json import WordPressJSONCollector, parse_wordpress_post, strip_html
from app.models.source import DiscoverySource
from tests.fixtures.wordpress_json_responses import (
    POSTS_RESPONSE_EMPTY,
    POSTS_RESPONSE_MISSING_LINK,
    POSTS_RESPONSE_ONE_POST,
)


def test_strip_html_removes_tags_and_collapses_whitespace():
    assert strip_html("<p>Hello <b>world</b>.</p>\n") == "Hello world ."


def test_strip_html_none_and_empty_pass_through():
    assert strip_html(None) is None
    assert strip_html("") is None


def test_parse_wordpress_post_extracts_core_fields():
    post = POSTS_RESPONSE_ONE_POST[0]
    record = parse_wordpress_post(post, site_url="https://example-cmt-org.org", org_name="Example CMT Org")
    assert record is not None
    assert record.external_id == "wp:https://example-cmt-org.org:43999"
    assert record.canonical_url == post["link"]
    assert record.title == "Research Update: New CMT1A Natural History Study"
    assert record.publisher == "Example CMT Org"
    assert "natural history study" in record.description
    assert record.publication_date is not None
    assert record.publication_date.isoformat() == "2026-09-01"
    assert record.raw_metadata["wp_post_id"] == 43999
    assert record.raw_metadata["org"] == "Example CMT Org"


def test_parse_wordpress_post_returns_none_when_link_missing():
    assert parse_wordpress_post(POSTS_RESPONSE_MISSING_LINK[0], site_url="https://x.org", org_name="X") is None


def _source(**config):
    return DiscoverySource(
        source_name="Test WP Org",
        source_type="wordpress_json",
        source_tier="tier_6",
        collection_method="official_api",
        base_url="https://example-cmt-org.org",
        configuration={"collector": "wordpress_json", **config},
    )


@respx.mock
async def test_collector_yields_records_from_posts_response():
    respx.route(method="GET", host="example-cmt-org.org", path__regex=r".*/wp-json/wp/v2/posts$").mock(
        return_value=httpx.Response(200, json=POSTS_RESPONSE_ONE_POST)
    )
    collector = WordPressJSONCollector(_source())
    results = [r async for r in collector.collect()]
    assert len(results) == 1
    assert results[0].raw_metadata["wp_post_id"] == 43999


@respx.mock
async def test_collector_handles_empty_posts_response():
    respx.route(method="GET", host="example-cmt-org.org", path__regex=r".*/wp-json/wp/v2/posts$").mock(
        return_value=httpx.Response(200, json=POSTS_RESPONSE_EMPTY)
    )
    collector = WordPressJSONCollector(_source())
    results = [r async for r in collector.collect()]
    assert results == []


@respx.mock
async def test_collector_stops_pagination_on_400_invalid_page():
    """WordPress returns 400 (rest_post_invalid_page_number) once `page`
    exceeds the available range -- a normal end-of-results signal, not an
    error that should abort collection."""
    route = respx.route(method="GET", host="example-cmt-org.org", path__regex=r".*/wp-json/wp/v2/posts$")
    route.side_effect = [
        httpx.Response(200, json=POSTS_RESPONSE_ONE_POST),
        httpx.Response(400, json={"code": "rest_post_invalid_page_number"}),
    ]
    collector = WordPressJSONCollector(_source(page_size=1, max_pages_per_run=5))
    results = [r async for r in collector.collect()]
    assert len(results) == 1


def test_collector_raises_without_site_url_or_base_url():
    import asyncio

    from app.collectors.base import CollectorError

    source = DiscoverySource(
        source_name="No URL Org",
        source_type="wordpress_json",
        source_tier="tier_6",
        collection_method="official_api",
        base_url=None,
        configuration={"collector": "wordpress_json"},
    )
    collector = WordPressJSONCollector(source)

    async def _drain():
        return [r async for r in collector.collect()]

    try:
        asyncio.run(_drain())
        assert False, "expected CollectorError"
    except CollectorError:
        pass
