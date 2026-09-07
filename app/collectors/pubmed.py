"""
PubMed / NCBI collector (spec #8) via the NCBI E-utilities Official API
(esearch -> efetch), which is the top of the collection priority order
(spec #7).

Vocabulary is configurable (spec #9): `source.configuration["vocabulary"]`
holds the search-term groups (disease names, gene symbols, etc.) rather
than a hard-coded list. See app/services/taxonomy_service.py for the
default seed vocabulary loaded into discovery_taxonomy on first run, which
this collector reads through the source configuration (a snapshot is
copied into the source's `configuration.vocabulary` at source-creation
time so a run is reproducible even if the taxonomy changes later; admins
can refresh it explicitly).

NOTE: requires network access and (optionally) an NCBI API key for higher
rate limits (PUBMED_API_KEY). This module could not be executed against
the live NCBI service from the sandbox this engine was built in -- see
IMPLEMENTATION_STATUS.md. Parsing logic is covered by unit tests using
recorded fixture XML (tests/fixtures/pubmed_responses.py).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import AsyncIterator, Optional

import httpx

from app.collectors.base import BaseCollector, CollectorError, NormalizedRecord, RateLimiter
from app.config import get_settings

ESEARCH_PATH = "/esearch.fcgi"
EFETCH_PATH = "/efetch.fcgi"

DEFAULT_VOCABULARY = {
    "disease": [
        "Charcot-Marie-Tooth",
        "CMT",
        "hereditary motor sensory neuropathy",
        "HMSN",
        "hereditary neuropathy",
    ],
    "genetics": [
        "PMP22", "MPZ", "GJB1", "MFN2", "SH3TC2", "GDAP1", "LITAF", "NEFL", "FIG4", "MORC2",
    ],
}


def build_query(vocabulary: dict[str, list[str]]) -> str:
    """Combine vocabulary groups with OR within a group, AND across groups
    is intentionally NOT applied by default (disease OR genetics terms are
    each independently CMT-relevant); callers can override via
    configuration["query_override"] for more precise control."""
    terms = []
    for group_terms in vocabulary.values():
        group_clause = " OR ".join(f'"{t}"[tiab]' for t in group_terms)
        if group_clause:
            terms.append(f"({group_clause})")
    return " OR ".join(terms)


class PubMedCollector(BaseCollector):
    collection_method = "official_api"

    def __init__(self, source, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(source, http_client)
        settings = get_settings()
        self.base_url = source.base_url or settings.pubmed_base_url
        self.api_key = settings.pubmed_api_key
        self.rate_limiter = RateLimiter(settings.pubmed_rate_limit_per_sec)
        self._owns_client = http_client is None
        self.client = http_client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def collect(self, since: Optional[datetime] = None) -> AsyncIterator[NormalizedRecord]:
        config = self.source.configuration or {}
        vocabulary = config.get("vocabulary") or DEFAULT_VOCABULARY
        query = config.get("query_override") or build_query(vocabulary)

        if since:
            date_filter = since.strftime("%Y/%m/%d")
            query = f"({query}) AND ({date_filter}:3000/01/01[pdat])"

        try:
            pmids = await self._esearch(query, retmax=config.get("page_size", 200))
            if not pmids:
                return
            for batch_start in range(0, len(pmids), 100):
                batch = pmids[batch_start : batch_start + 100]
                async for record in self._efetch(batch):
                    yield record
        except httpx.HTTPError as exc:
            raise CollectorError(f"PubMed collection failed: {exc}") from exc
        finally:
            if self._owns_client:
                await self.client.aclose()

    async def _esearch(self, query: str, retmax: int = 200) -> list[str]:
        await self.rate_limiter.wait()
        params = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": str(retmax),
            "sort": "most+recent",
        }
        if self.api_key:
            params["api_key"] = self.api_key
        resp = await self.client.get(ESEARCH_PATH, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("esearchresult", {}).get("idlist", [])

    async def _efetch(self, pmids: list[str]) -> AsyncIterator[NormalizedRecord]:
        await self.rate_limiter.wait()
        params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
        if self.api_key:
            params["api_key"] = self.api_key
        resp = await self.client.get(EFETCH_PATH, params=params)
        resp.raise_for_status()
        for record in parse_pubmed_xml(resp.text):
            yield record


def _text(el, path, default=None):
    node = el.find(path)
    return node.text if node is not None and node.text else default


def parse_pubmed_xml(xml_text: str) -> list[NormalizedRecord]:
    """
    Pure parsing function, deliberately separated from the network call so
    it can be unit-tested against fixture XML without any network access
    (spec #45/#48).
    """
    root = ET.fromstring(xml_text)
    records: list[NormalizedRecord] = []

    for article in root.findall(".//PubmedArticle"):
        medline = article.find("MedlineCitation")
        if medline is None:
            continue
        pmid = _text(medline, "PMID")
        art = medline.find("Article")
        if art is None or pmid is None:
            continue

        title = _text(art, "ArticleTitle")

        abstract_parts = [
            (node.text or "") for node in art.findall("Abstract/AbstractText")
        ]
        abstract = " ".join(p for p in abstract_parts if p).strip() or None

        journal = _text(art, "Journal/Title")

        authors = []
        for author in art.findall("AuthorList/Author"):
            last = _text(author, "LastName")
            fore = _text(author, "ForeName")
            if last and fore:
                authors.append(f"{fore} {last}")
            elif last:
                authors.append(last)
            else:
                collective = _text(author, "CollectiveName")
                if collective:
                    authors.append(collective)

        doi = None
        for aid in art.findall("ELocationID"):
            if aid.attrib.get("EIdType") == "doi":
                doi = aid.text
        if doi is None:
            for aid in article.findall(".//ArticleId"):
                if aid.attrib.get("IdType") == "doi":
                    doi = aid.text

        pub_date = _parse_pub_date(art.find("Journal/JournalIssue/PubDate"))

        records.append(
            NormalizedRecord(
                external_id=pmid,
                canonical_url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                title=title,
                authors=authors,
                journal=journal,
                doi=doi,
                pmid=pmid,
                publication_date=pub_date,
                abstract=abstract,
                raw_metadata={"source": "pubmed"},
            )
        )
    return records


def _parse_pub_date(node) -> Optional[date]:
    if node is None:
        return None
    year = _text(node, "Year")
    month = _text(node, "Month") or "01"
    day = _text(node, "Day") or "01"
    if not year:
        medline_date = _text(node, "MedlineDate")
        if medline_date and len(medline_date) >= 4 and medline_date[:4].isdigit():
            year = medline_date[:4]
        else:
            return None
    month_map = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
        "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    month_num = month_map.get(month, month if month.isdigit() else "01")
    try:
        return date(int(year), int(month_num), int(day) if day.isdigit() else 1)
    except (ValueError, TypeError):
        try:
            return date(int(year), 1, 1)
        except (ValueError, TypeError):
            return None
