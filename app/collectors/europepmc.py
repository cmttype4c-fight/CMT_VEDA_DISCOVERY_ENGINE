"""
Europe PMC collector (CMT-specific overhaul, Phase 3A: scientific
literature source expansion) via Europe PMC's official REST API
(www.ebi.ac.uk/europepmc/webservices/rest) -- a free, public, long-stable
(operating since ~2012) API with no authentication required for search,
maintained by EMBL-EBI. This is the second "Official API" tier
literature source alongside PubMed (spec #7 priority: Official API is
top tier), and doubles as this engine's route to open-access full text
(see app/services/fulltext/resolver.py), since Europe PMC's `core`
result type reports `isOpenAccess` and, when true, a `fullTextUrlList`.

CONFIDENCE CAVEAT (same discipline as app/collectors/clinicaltrials.py's
`since`-handling note): the request/response shape below matches Europe
PMC's long-documented REST API contract as of this codebase's training
knowledge, but -- per the standing sandbox constraint (no network access,
reconfirmed every revision, see IMPLEMENTATION_STATUS.md) -- it has NOT
been exercised against the live service. Unlike ClinicalTrials.gov's v2
API (which this codebase deliberately treated with extra caution because
it is comparatively new and unverified), Europe PMC's core search
contract has been stable for many years and is broadly relied upon by
other bioinformatics tooling, so implementing it now is a reasonable risk
-- but it is still unverified, still disabled by default (no
`discovery_sources` row is created for it by this pass), and MUST be
smoke-tested against the real API before being enabled in production.
Parsing is unit-tested against fixture JSON
(tests/fixtures/europepmc_responses.py), same pattern as every other
collector.

Retrieval query mirrors the same Phase 2A principle as PubMed's
build_query: disease/subtype vocabulary only, not gene terms, so a
gene-only match cannot pull a document into the pool.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, AsyncIterator, Optional

import httpx

from app.collectors.base import BaseCollector, CollectorError, NormalizedRecord, RateLimiter
from app.config import get_settings

SEARCH_PATH = "/search"

# FINAL CORRECTIVE PROMPT #4: same fix as
# app/collectors/pubmed.py/clinicaltrials.py -- bare "CMT" and generic
# "hereditary neuropathy" removed as independent retrieval drivers.
DEFAULT_QUERY_TERMS = [
    "Charcot-Marie-Tooth", "Charcot Marie Tooth", "hereditary motor sensory neuropathy", "HMSN",
]


def build_query(terms: list[str]) -> str:
    clause = " OR ".join(f'"{t}"' for t in terms)
    return f"({clause})" if clause else ""


class EuropePMCCollector(BaseCollector):
    collection_method = "official_api"

    def __init__(self, source, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(source, http_client)
        settings = get_settings()
        self.base_url = source.base_url or settings.europepmc_base_url
        self.rate_limiter = RateLimiter(settings.europepmc_rate_limit_per_sec)
        self._owns_client = http_client is None
        self.client = http_client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def collect(self, since: Optional[datetime] = None) -> AsyncIterator[NormalizedRecord]:
        config = self.source.configuration or {}
        terms = config.get("query_terms") or DEFAULT_QUERY_TERMS
        query = config.get("query_override") or build_query(terms)
        if since:
            date_filter = since.strftime("%Y-%m-%d")
            query = f"({query}) AND (FIRST_PDATE:[{date_filter} TO 3000-01-01])"

        page_size = min(int(config.get("page_size", 100)), 1000)

        try:
            cursor = "*"
            fetched = 0
            max_records = int(config.get("max_records_per_run", 500))
            while True:
                await self.rate_limiter.wait()
                params: dict[str, Any] = {
                    "query": query,
                    "format": "json",
                    "resultType": "core",
                    "pageSize": page_size,
                    "cursorMark": cursor,
                }
                resp = await self.client.get(SEARCH_PATH, params=params)
                resp.raise_for_status()
                data = resp.json()

                results = (data.get("resultList") or {}).get("result", [])
                for item in results:
                    record = parse_europepmc_result(item)
                    if record:
                        yield record
                        fetched += 1

                next_cursor = data.get("nextCursorMark")
                if not next_cursor or next_cursor == cursor or not results or fetched >= max_records:
                    break
                cursor = next_cursor
        except httpx.HTTPError as exc:
            raise CollectorError(f"Europe PMC collection failed: {exc}") from exc
        finally:
            if self._owns_client:
                await self.client.aclose()


def parse_europepmc_result(item: dict[str, Any]) -> Optional[NormalizedRecord]:
    """Pure parsing function, unit-testable without network access."""
    ext_id = item.get("id") or item.get("pmid") or item.get("doi")
    if not ext_id:
        return None

    pmid = item.get("pmid")
    doi = item.get("doi")
    title = item.get("title")
    journal_info = item.get("journalInfo") or {}
    journal = (journal_info.get("journal") or {}).get("title")

    authors = []
    for author in (item.get("authorList") or {}).get("author", []) or []:
        full_name = author.get("fullName")
        if full_name:
            authors.append(full_name)

    pub_date = _parse_date(item.get("firstPublicationDate"))

    canonical_url = (
        f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid
        else f"https://europepmc.org/article/{item.get('source', 'MED')}/{ext_id}"
    )

    is_open_access = str(item.get("isOpenAccess", "")).upper() == "Y"
    full_text_urls = [
        u.get("url") for u in (item.get("fullTextUrlList") or {}).get("fullTextUrl", []) or [] if u.get("url")
    ]

    return NormalizedRecord(
        external_id=str(ext_id),
        canonical_url=canonical_url,
        title=title,
        authors=authors,
        journal=journal,
        doi=doi,
        pmid=pmid,
        publication_date=pub_date,
        abstract=item.get("abstractText"),
        raw_metadata={
            "source": "europepmc",
            "is_open_access": is_open_access,
            "full_text_urls": full_text_urls,
            "pmcid": item.get("pmcid"),
        },
    )


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(value[:4], "%Y").date()
        except ValueError:
            return None
