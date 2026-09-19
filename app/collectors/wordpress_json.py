"""
WordPress REST API collector -- FINAL FOCUSED CORRECTION, TASK 5: "for
each individual source, determine whether a legitimate official/public
mechanism exists... official public data endpoint" is explicitly listed
as an acceptable mechanism alongside an official API or RSS/Atom feed.

The WordPress REST API (`/wp-json/wp/v2/posts`) is a core, official,
publicly documented part of WordPress itself (developer.wordpress.org/
rest-api/) -- enabled by default on every WordPress site unless the site
owner explicitly disables it. It is not scraping: it is a stable, typed
JSON endpoint the platform ships specifically for external programmatic
consumption, requiring no authentication for public post content.

VERIFICATION, done properly this pass (not assumed): several CMT
ecosystem organizations' sites were confirmed to run WordPress in a
previous pass (from `wp-content` asset paths), but this pass went
further -- for each one, BOTH `{base_url}/wp-json/wp/v2/posts?per_page=1`
AND `{base_url}/robots.txt` were actually fetched live (via this
session's web-fetch tooling, not this collector's own httpx code -- see
the confidence note below) this pass:

  - CMTA (cmtausa.org): robots.txt EXPLICITLY disallows `/wp-json/`
    (`Disallow: /wp-json/`) AND `*/feed/`. Per the corrective prompt's
    own instruction ("do not scrape or bypass... access controls"),
    CMTA is NOT implemented via this or any mechanism -- the site
    owner's own robots.txt is a clear access-control signal against it,
    for both this mechanism and the RSS mechanism tried in a previous
    pass. Stays pending; see IMPLEMENTATION_STATUS.md.
  - HNF/CureCMT (curecmt.org): `/wp-json/wp/v2/posts` returned a 404
    (REST API not present/enabled, or not WordPress at this path) --
    consistent with a previous pass's finding that `/feed/` also 404'd.
    Stays pending.
  - CMTRF (cmtrf.org): live JSON confirmed (a real, current post was
    returned), robots.txt does not disallow `/wp-json/`. Implemented.
  - ECMTF (ecmtf.org): live JSON confirmed, robots.txt permissive.
    Implemented.
  - CMT Australia (cmtaustralia.org.au): live JSON confirmed, robots.txt
    permissive (only blocks unrelated WooCommerce/admin paths).
    Implemented.
  - Peripheral Nerve Society (pnsociety.com): live JSON confirmed,
    robots.txt permissive. Implemented.
  - Neurology Asia: confirmed NOT WordPress (custom PHP site, no
    `wp-json`/`wp-content` signature) -- this collector does not apply;
    stays pending under a different, unimplemented mechanism
    (`controlled_webpage_extraction`).

CONFIDENCE, stated precisely: the four "Implemented" sites' endpoints
were confirmed LIVE this pass -- real, current JSON was actually
fetched and inspected, not merely documented behavior assumed from
WordPress's general API contract (a materially stronger starting point
than any other collector in this codebase has ever had). This
collector's own Python code (using httpx below, not the web-fetch tool
used for verification) has, like every other collector here, never
itself been executed against these endpoints -- this sandbox's
shell-level network access remains proxy-blocked (see
IMPLEMENTATION_STATUS.md) -- but the endpoint shape it codes against is
not a guess.
"""
from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any, AsyncIterator, Optional

import httpx

from app.collectors.base import BaseCollector, CollectorError, NormalizedRecord, RateLimiter

POSTS_PATH = "/wp-json/wp/v2/posts"


class _TagStripper(HTMLParser):
    """Minimal HTML-to-text for a WP post's `content.rendered`/
    `excerpt.rendered` (which are always HTML). Deliberately simpler than
    app/services/fulltext/extraction.py's extractor (no script/nav
    skipping needed -- WP REST API post bodies are just article markup,
    not a full page)."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return " ".join(self._parts)


def strip_html(html_fragment: Optional[str]) -> Optional[str]:
    if not html_fragment:
        return None
    parser = _TagStripper()
    try:
        parser.feed(html_fragment)
    except Exception:  # noqa: BLE001 - never let a malformed fragment abort collection
        return html_fragment
    return parser.text() or None


class WordPressJSONCollector(BaseCollector):
    """
    Generic collector for any WordPress site's official REST API,
    configured per-source via `source.configuration["site_url"]` (or
    falling back to `source.base_url`) -- same "one implementation,
    configured per source" pattern as GenericRSSCollector
    (app/collectors/generic_rss.py), so onboarding another
    WordPress-based CMT organization in the future needs zero new code,
    only a new `discovery_sources` row (after the same live
    verify-the-endpoint-and-check-robots.txt diligence documented above).
    """

    collection_method = "official_api"

    def __init__(self, source, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(source, http_client)
        config = source.configuration or {}
        self.site_url = (config.get("site_url") or source.base_url or "").rstrip("/")
        self.rate_limiter = RateLimiter(config.get("rate_limit_per_sec", 1))
        self._owns_client = http_client is None
        self.client = http_client or httpx.AsyncClient(timeout=30.0)

    async def collect(self, since: Optional[datetime] = None) -> AsyncIterator[NormalizedRecord]:
        if not self.site_url:
            raise CollectorError(f"Source {self.source.source_name} has no site_url/base_url configured")

        config = self.source.configuration or {}
        per_page = min(int(config.get("page_size", 20)), 100)
        max_pages = int(config.get("max_pages_per_run", 5))

        params: dict[str, Any] = {"per_page": per_page, "orderby": "date", "order": "desc"}
        if since:
            # WordPress REST API's documented `after`/`before` date-range
            # filters (ISO 8601) -- a stable, long-standing part of the
            # core `wp/v2/posts` contract, same confidence tier as this
            # codebase's use of PubMed's `[pdat]` field.
            params["after"] = since.isoformat()

        try:
            for page in range(1, max_pages + 1):
                await self.rate_limiter.wait()
                resp = await self.client.get(
                    f"{self.site_url}{POSTS_PATH}", params={**params, "page": page}
                )
                if resp.status_code == 400:
                    # WordPress returns 400 (rest_post_invalid_page_number)
                    # once `page` exceeds the available range -- a normal
                    # end-of-results signal here, not an error.
                    break
                resp.raise_for_status()
                posts = resp.json()
                if not posts:
                    break
                for post in posts:
                    record = parse_wordpress_post(post, site_url=self.site_url, org_name=self.source.source_name)
                    if record:
                        yield record
                if len(posts) < per_page:
                    break
        except httpx.HTTPError as exc:
            raise CollectorError(f"WordPress REST API collection failed for {self.site_url}: {exc}") from exc
        finally:
            if self._owns_client:
                await self.client.aclose()


def parse_wordpress_post(post: dict[str, Any], *, site_url: str, org_name: str) -> Optional[NormalizedRecord]:
    """Pure parsing function, unit-testable offline against recorded
    fixture JSON (tests/fixtures/wordpress_json_responses.py)."""
    post_id = post.get("id")
    link = post.get("link")
    if post_id is None or not link:
        return None

    title = strip_html((post.get("title") or {}).get("rendered"))
    excerpt = strip_html((post.get("excerpt") or {}).get("rendered"))
    content = strip_html((post.get("content") or {}).get("rendered"))
    description = excerpt or content

    pub_date = _parse_wp_date(post.get("date_gmt") or post.get("date"))

    return NormalizedRecord(
        external_id=f"wp:{site_url}:{post_id}",
        canonical_url=link,
        title=title,
        authors=[],
        publisher=org_name,
        publication_date=pub_date,
        description=description,
        raw_metadata={"source": "wordpress_json", "site_url": site_url, "wp_post_id": post_id, "org": org_name},
    )


def _parse_wp_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        # WP dates are ISO 8601 without a UTC suffix even for `date_gmt`
        # (e.g. "2026-09-09T12:38:24") -- strip any trailing "Z" defensively.
        return datetime.fromisoformat(value.replace("Z", "")).date()
    except ValueError:
        return None
