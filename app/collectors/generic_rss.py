"""
Generic RSS/Atom collector (spec #7 priority tier 2, #8).

Covers the "extensible collector interfaces" requirement for sources like
CMT Research Foundation, CMTA, HNF, and journal feeds that publish
RSS/Atom -- a single implementation configured per-source via
`source.configuration["feed_url"]`, rather than one class per
organisation. Sources needing bespoke handling (e.g. an org with no feed
at all) get a dedicated collector later; this one intentionally stays
generic per spec #8 ("first release does NOT need every possible
collector to be production-live... architecture must make adding them
straightforward").
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import AsyncIterator, Optional

import feedparser
import httpx

from app.collectors.base import BaseCollector, CollectorError, NormalizedRecord, RateLimiter


class GenericRSSCollector(BaseCollector):
    collection_method = "rss_atom"

    def __init__(self, source, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(source, http_client)
        self.rate_limiter = RateLimiter(source.configuration.get("rate_limit_per_sec", 1))
        self._owns_client = http_client is None
        self.client = http_client or httpx.AsyncClient(timeout=30.0)

    async def collect(self, since: Optional[datetime] = None) -> AsyncIterator[NormalizedRecord]:
        feed_url = self.source.configuration.get("feed_url") or self.source.base_url
        if not feed_url:
            raise CollectorError(f"Source {self.source.source_name} has no feed_url configured")

        try:
            await self.rate_limiter.wait()
            resp = await self.client.get(feed_url)
            resp.raise_for_status()
            for record in parse_feed(resp.text, feed_url):
                yield record
        except httpx.HTTPError as exc:
            raise CollectorError(f"RSS collection failed for {feed_url}: {exc}") from exc
        finally:
            if self._owns_client:
                await self.client.aclose()


def parse_feed(raw_feed_text: str, feed_url: str) -> list[NormalizedRecord]:
    """Pure parsing function, unit-testable offline."""
    parsed = feedparser.parse(raw_feed_text)
    records = []
    for entry in parsed.entries:
        link = entry.get("link")
        external_id = entry.get("id") or link or hashlib.sha256(
            (entry.get("title", "") + feed_url).encode("utf-8")
        ).hexdigest()

        pub_date = None
        if entry.get("published_parsed"):
            pub_date = datetime(*entry["published_parsed"][:6]).date()

        records.append(
            NormalizedRecord(
                external_id=str(external_id),
                canonical_url=link,
                title=entry.get("title"),
                authors=[entry.get("author")] if entry.get("author") else [],
                publication_date=pub_date,
                description=entry.get("summary"),
                raw_metadata={"source": "rss", "feed_url": feed_url},
            )
        )
    return records
