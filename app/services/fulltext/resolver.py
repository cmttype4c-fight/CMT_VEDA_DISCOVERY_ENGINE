"""
Full-text resolution (CMT-specific overhaul, Phase 5; EUROPE PMC
FULL-TEXT CORRECTION pass: official XML fallback).

Determines, for a given candidate, whether legally-accessible full text
exists and where -- without downloading it (see service.py for the
download/store/provenance step that follows). Acquisition priority, per
spec:

    1. Official/open-access source
    2. PMC / Europe PMC where available and permitted
    3. Publisher/repository full-text source where permitted
    4. No accessible PDF -> full_text_available = False (not an error)

This pass implements only priority tiers 1-2, both via Europe PMC's
`core` search result (which already reports `isOpenAccess` and a
`fullTextUrlList` when true) -- the same API app/collectors/europepmc.py
uses for retrieval, so a candidate that was itself discovered via Europe
PMC already carries this in `raw_metadata`; for a PubMed-discovered
candidate (no Europe PMC raw_metadata), this module makes one additional
lookup call by PMID/DOI. Tier 3 (arbitrary publisher/repository access)
is explicitly NOT implemented -- that would mean per-publisher scraping
logic this pass has no way to verify is permitted, which the instruction
is explicit about not doing ("must NOT bypass paywalls").

EUROPE PMC FULL-TEXT CORRECTION (this pass): real VPS testing against
four live PMC articles (PMC13571996, PMC13563502, PMC13570443,
PMC13578534) found that Europe PMC's PDF *render* URL
(`https://europepmc.org/articles/{PMCID}?pdf=render`) returns HTTP 403
from the VPS, while the official, documented
`https://www.ebi.ac.uk/europepmc/webservices/rest/{PMCID}/fullTextXML`
endpoint returns HTTP 200 with substantial full-text XML for all four.
Previously, `resolve_full_text` picked exactly ONE url (the single
highest-priority format found in Europe PMC's own `fullTextUrlList`) and
`service.py::acquire_full_text` had no fallback if that one download
failed -- a PDF 403 was a terminal failure even when XML full text was
genuinely available. This is fixed by resolving an ORDERED CHAIN of
candidates (`ResolutionResult.candidates`, built by `_build_candidates`
below) instead of a single URL: PDF (if Europe PMC's own metadata offers
one) -> the OFFICIAL fullTextXML REST endpoint keyed by PMCID (used
whenever a PMCID is available at all, independently of whatever
`fullTextUrlList` happens to list for this particular result -- this is
the "official public data endpoint" mechanism the correction calls for,
not a scrape) -> HTML (if offered) -> any other listed URL, honestly
labeled "other". `service.py` now walks this chain, and a PDF request
that fails at the HTTP layer (403, or any other transport/HTTP error)
falls through to the next candidate rather than terminally failing the
candidate -- see that module's docstring for the exact walk/accept
logic, including how a successfully-downloaded-but-differently-shaped
response (e.g. Europe PMC's metadata says "pdf" but the bytes are
genuinely something else) is still handled the same accept-and-correct
way it always was (never rejected outright just for not matching the
claim -- see FINAL CORRECTIVE PROMPT #3/#6, unchanged by this pass).

`_pick_best_url` (below) is UNCHANGED and kept for its own direct unit
tests (tests/test_fulltext.py) and as the simple single-best-format
picker it always was; `resolve_full_text` no longer calls it internally
(superseded by `_build_candidates`, which needs to consider PMCID/the
official XML endpoint, not just `fullTextUrlList` entries), but nothing
about its own behavior changed.

CONFIDENCE CAVEAT: same as app/collectors/europepmc.py for the `/search`
lookup itself (long-stable Europe PMC REST contract, still not
exercised against the live service from this sandbox's Bash-level
network, which remains blocked -- see IMPLEMENTATION_STATUS.md). The
`fullTextXML` endpoint specifically, however, WAS live-tested this pass
(via the VPS, per the correction's own report, and independently via
this session's web-fetch tooling -- see IMPLEMENTATION_STATUS.md for the
exact PMCID/character-count evidence), so it carries materially higher
confidence than the rest of this module's unverified-from-this-sandbox
caveat.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.config import get_settings
from app.models.candidate import DiscoveryCandidate


@dataclass
class FullTextCandidate:
    """One attempt in the PDF -> XML -> HTML fallback chain. `full_text_format`
    is what this candidate CLAIMS to be based on Europe PMC's own metadata
    (or, for the official XML endpoint, what it is by construction) --
    service.py still verifies the actual downloaded bytes independently
    (app/services/fulltext/content_sniff.py) and never trusts this label
    alone, exactly as the single-URL design always did."""

    url: str
    mime_type: Optional[str]
    full_text_format: str  # "pdf" | "xml" | "html" | "other"
    full_text_source: str


@dataclass
class ResolutionResult:
    available: bool
    # Highest-priority candidate's fields, kept at the top level for
    # backward compatibility with existing callers/tests that read
    # `.url`/`.mime_type`/`.full_text_format`/`.full_text_source`
    # directly (e.g. tests/test_fulltext.py) -- always equal to
    # `candidates[0]`'s fields when `candidates` is non-empty.
    url: Optional[str] = None
    mime_type: Optional[str] = None
    full_text_format: Optional[str] = None  # "pdf" | "xml" | "html" | other explicit value
    full_text_source: Optional[str] = None
    reason: Optional[str] = None
    # NEW this pass: the full ordered fallback chain. service.py walks
    # this list, trying each candidate in turn, rather than treating the
    # first (highest-priority) one's failure as terminal.
    candidates: list[FullTextCandidate] = field(default_factory=list)


# Priority order per the corrective prompt (#5): "PDF -> XML -> HTML". PDF
# is preferred when genuinely available, but a candidate is never rejected
# for lacking one -- XML, then HTML, are legitimate, accurately-labeled
# fallbacks; the actual format obtained is always recorded truthfully
# (never reported as "pdf" when it is not).
_FORMAT_PRIORITY: list[tuple[str, str]] = [
    ("pdf", "application/pdf"),
    ("xml", "application/xml"),
    ("html", "text/html"),
]


def _pick_best_url(full_text_urls: list[dict]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Picks the highest-priority available format: PDF, then XML, then
    HTML. Returns (url, mime_type, full_text_format); full_text_format
    always accurately reflects what was actually selected, never assumed
    to be a PDF when it isn't.

    UNCHANGED this pass -- kept for its own direct unit tests and as a
    simple "what's the single best format Europe PMC's own metadata
    offers" helper. `resolve_full_text` now builds the full fallback
    chain via `_build_candidates` instead of calling this, since the
    chain also needs to consider the PMCID-keyed official XML endpoint,
    which isn't necessarily present in `full_text_urls` at all."""
    by_style: dict[str, str] = {}
    for entry in full_text_urls:
        style = (entry.get("documentStyle") or "").lower()
        url = entry.get("url")
        if style and url and style not in by_style:
            by_style[style] = url

    for style, mime in _FORMAT_PRIORITY:
        if style in by_style:
            return by_style[style], mime, style

    # Some styles (e.g. "doi", "externallink") aren't one of our three
    # tracked formats -- fall back to the first URL present at all, but
    # record its format honestly as "other" rather than guessing.
    for entry in full_text_urls:
        if entry.get("url"):
            return entry["url"], None, "other"

    return None, None, None


def _build_candidates(item: dict, *, europepmc_base_url: str) -> list[FullTextCandidate]:
    """
    Builds the ordered PDF -> XML -> HTML (-> other) fallback chain for
    one Europe PMC search result `item`.

    The XML candidate is deliberately NOT limited to whatever
    `fullTextUrlList` happens to list for this particular result: per
    this correction, the OFFICIAL `fullTextXML` REST endpoint
    (documented at
    https://europepmc.org/RestfulWebService#!/Europe32PMC32Articles32RESTful32API/fullTextXML,
    and live-verified this pass against four real PMCIDs -- see
    IMPLEMENTATION_STATUS.md) is used whenever the result carries a
    PMCID at all, since that endpoint serves full-text XML for any
    article in PubMed Central by construction, independent of whether
    Europe PMC's search-result metadata also happened to list an "xml"
    documentStyle entry. Only when no PMCID is present at all does this
    fall back to a `fullTextUrlList` "xml" entry, if one exists.
    """
    full_text_urls = (item.get("fullTextUrlList") or {}).get("fullTextUrl", []) or []
    by_style: dict[str, str] = {}
    for entry in full_text_urls:
        style = (entry.get("documentStyle") or "").lower()
        url = entry.get("url")
        if style and url and style not in by_style:
            by_style[style] = url

    candidates: list[FullTextCandidate] = []

    # 1. PDF -- only a genuinely PDF-labeled URL from Europe PMC's own
    #    metadata. "use PDF when a genuinely accessible PDF is available"
    #    -- accessibility (does the download actually succeed, and is
    #    the content actually a PDF) is verified downstream in
    #    service.py, never assumed here.
    if "pdf" in by_style:
        candidates.append(FullTextCandidate(by_style["pdf"], "application/pdf", "pdf", "europepmc_oa_pdf"))

    # 2. XML -- the official fullTextXML endpoint, preferred over
    #    whatever fullTextUrlList lists (see docstring above).
    pmcid = item.get("pmcid")
    if pmcid:
        candidates.append(
            FullTextCandidate(
                f"{europepmc_base_url}/{pmcid}/fullTextXML",
                "application/xml",
                "xml",
                "europepmc_fulltextxml_api",
            )
        )
    elif "xml" in by_style:
        candidates.append(FullTextCandidate(by_style["xml"], "application/xml", "xml", "europepmc_oa_xml"))

    # 3. HTML -- last legitimate full-text fallback, only once PDF and
    #    XML are both unavailable/exhausted.
    if "html" in by_style:
        candidates.append(FullTextCandidate(by_style["html"], "text/html", "html", "europepmc_oa_html"))

    # 4. Anything else Europe PMC listed (e.g. "doi"/"externallink")
    #    that isn't one of the three tracked formats -- lowest priority,
    #    kept for parity with the previous single-URL design's final
    #    fallback, labeled honestly as "other" rather than guessed.
    for entry in full_text_urls:
        style = (entry.get("documentStyle") or "").lower()
        url = entry.get("url")
        if url and style not in ("pdf", "xml", "html"):
            candidates.append(FullTextCandidate(url, None, "other", "europepmc_oa_other"))
            break

    return candidates


async def resolve_full_text(
    candidate: DiscoveryCandidate, *, http_client: Optional[httpx.AsyncClient] = None
) -> ResolutionResult:
    if not candidate.doi and not candidate.pmid:
        return ResolutionResult(False, reason="no DOI or PMID to resolve against")

    settings = get_settings()
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(base_url=settings.europepmc_base_url, timeout=30.0)

    try:
        if candidate.pmid:
            query = f"EXT_ID:{candidate.pmid} AND SRC:MED"
        else:
            query = f'DOI:"{candidate.doi}"'

        resp = await client.get(
            "/search", params={"query": query, "format": "json", "resultType": "core", "pageSize": 1}
        )
        resp.raise_for_status()
        data = resp.json()
        results = (data.get("resultList") or {}).get("result", [])
        if not results:
            return ResolutionResult(False, reason="not found in Europe PMC")

        item = results[0]
        is_open_access = str(item.get("isOpenAccess", "")).upper() == "Y"
        if not is_open_access:
            return ResolutionResult(False, reason="not open access per Europe PMC")

        candidates = _build_candidates(item, europepmc_base_url=settings.europepmc_base_url)
        if not candidates:
            return ResolutionResult(False, reason="marked open access but no full-text URL returned")

        top = candidates[0]
        return ResolutionResult(
            True, url=top.url, mime_type=top.mime_type, full_text_format=top.full_text_format,
            full_text_source=top.full_text_source, candidates=candidates,
        )
    except httpx.HTTPError as exc:
        # A resolution failure is not a candidate-invalidating error --
        # the candidate stays valid, full_text_available just stays False.
        return ResolutionResult(False, reason=f"resolution request failed: {exc}")
    finally:
        if owns_client:
            await client.aclose()
