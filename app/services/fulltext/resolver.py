"""
Full-text resolution (CMT-specific overhaul, Phase 5).

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

CONFIDENCE CAVEAT: same as app/collectors/europepmc.py -- Europe PMC's
REST contract is long-stable but unverified live from this sandbox (no
network access). Never exercised against the real service; unit-tested
against fixture JSON only.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx

from app.config import get_settings
from app.models.candidate import DiscoveryCandidate


@dataclass
class ResolutionResult:
    available: bool
    url: Optional[str] = None
    mime_type: Optional[str] = None
    full_text_format: Optional[str] = None  # "pdf" | "xml" | "html" | other explicit value
    full_text_source: Optional[str] = None
    reason: Optional[str] = None


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
    to be a PDF when it isn't."""
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

        full_text_urls = (item.get("fullTextUrlList") or {}).get("fullTextUrl", []) or []
        url, mime_type, full_text_format = _pick_best_url(full_text_urls)
        if not url:
            return ResolutionResult(False, reason="marked open access but no full-text URL returned")

        return ResolutionResult(
            True, url=url, mime_type=mime_type, full_text_format=full_text_format,
            full_text_source="europepmc_oa",
        )
    except httpx.HTTPError as exc:
        # A resolution failure is not a candidate-invalidating error --
        # the candidate stays valid, full_text_available just stays False.
        return ResolutionResult(False, reason=f"resolution request failed: {exc}")
    finally:
        if owns_client:
            await client.aclose()
