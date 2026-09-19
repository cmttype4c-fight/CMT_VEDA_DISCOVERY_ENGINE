"""
ClinVar collector -- FINAL FOCUSED CORRECTION, TASK 5: "actually
investigate each source's available official mechanism and implement it
where technically and legally appropriate."

ClinVar is accessed via the same official NCBI E-utilities Official API
family already used for PubMed (app/collectors/pubmed.py) -- `db=clinvar`
instead of `db=pubmed` -- documented at
https://www.ncbi.nlm.nih.gov/clinvar/docs/programmatic_access/. No
authentication is required (an optional API key raises rate limits, same
as PubMed's `PUBMED_API_KEY` pattern -- reused here as `CLINVAR_API_KEY`
for clarity even though it's the same underlying NCBI key mechanism).

CONFIDENCE, stated precisely (same discipline as every other collector in
this codebase): unlike PubMed/ClinicalTrials/Europe PMC, which could only
ever be checked against NCBI/EBI's own published documentation from this
sandbox, ClinVar's `esummary` response shape below was LIVE-FETCHED and
confirmed during this pass (via this session's web-fetch tooling, which
is not subject to the sandbox's shell-level network block -- see
IMPLEMENTATION_STATUS.md) against a real record
(https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=clinvar&id=65533&retmode=json),
confirming the exact field names `germline_classification` (containing
`description`, `trait_set[].trait_name`), `genes[].symbol`, `title`,
`accession_version`, and `obj_type` used by `parse_clinvar_summary()`
below. This is genuinely stronger verification than this codebase's other
collectors have ever had, but the `esearch` call itself (finding record
IDs from a disease-name query) was NOT independently live-verified in
this pass -- a follow-up fetch to confirm it was blocked by this specific
endpoint's robots.txt as interpreted by this session's web-fetch tool
(inconsistent with the esummary fetch succeeding moments earlier from the
same host/subdomain, so this is very plausibly a fetch-tool-side
crawling policy rather than a genuine NCBI API restriction -- NCBI's own
documentation explicitly describes esearch as freely available -- but it
was not independently confirmed live either way). The `[dis]` search
field tag used below is documented in NCBI's ClinVar search-field help
(a long-standing, stable field-tag convention, same confidence tier as
this codebase's existing use of PubMed's `[tiab]`/`[pdat]` tags). This
collector's own Python code (using httpx, not the web-fetch tool) has,
like every other collector in this repository, never been executed
against the live service -- this sandbox's shell-level network access
remains proxy-blocked (see IMPLEMENTATION_STATUS.md).

SHAPE MISMATCH, and how it's handled: a ClinVar record is a genetic
variant/classification, not an article or a trial -- it has no abstract,
no PDF, no full text to acquire. This collector deliberately produces a
`NormalizedRecord` whose `description` is a short, human-readable
synopsis built from the variant title, gene, and classification (there is
nothing else to summarize), and whose `raw_metadata["conditions"]` holds
the record's structured trait/condition names -- EXACTLY the same
`raw_metadata["conditions"]` key ClinicalTrials.gov's collector already
populates (app/collectors/clinicaltrials.py), which
app/services/intelligence/eligibility.py already knows how to consult
as a structured-conditions signal, taking priority over free-text
matching. This means ClinVar plugs into the EXISTING eligibility gate
with ZERO changes to eligibility.py itself -- a ClinVar record is only
eligible when its own structured trait/condition list actually names CMT
(full form, an unambiguous clinical synonym, or a subtype code), the same
strict rule as every other source; a variant in an unrelated gene that
happens to be *associated* with CMT in the general literature, but whose
OWN listed condition is something else, is correctly rejected, same as
an unrelated ClinicalTrials.gov trial would be.

Downstream, `app/services/candidate_service.py` maps this collector to a
new `genetic_variant` content type (app/models/enums.py), which
`app/worker/handlers.py::handle_analyse_candidate` correctly routes
straight to editorial-draft generation without attempting full-text
resolution (only `research_paper` candidates do that) -- no change was
needed there; it already branches on content_type.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator, Optional

import httpx

from app.collectors.base import BaseCollector, CollectorError, NormalizedRecord, RateLimiter
from app.config import get_settings

ESEARCH_PATH = "/esearch.fcgi"
ESUMMARY_PATH = "/esummary.fcgi"

# Same disease-only, full-form-first principle as PubMed/ClinicalTrials/
# Europe PMC's DEFAULT_VOCABULARY/DEFAULT_QUERY_TERM(S) -- see
# app/collectors/pubmed.py's build_query docstring. Gene symbols are
# deliberately NOT part of this retrieval query (a gene-only ClinVar
# search would pull in every variant ever reported for e.g. PMP22,
# the overwhelming majority unrelated to CMT specifically) -- genes stay
# a downstream eligibility signal only, exactly as elsewhere in this
# codebase.
DEFAULT_DISEASE_TERMS = [
    "Charcot-Marie-Tooth",
    "Charcot Marie Tooth",
    "hereditary motor sensory neuropathy",
]


def build_query(terms: list[str]) -> str:
    clause = " OR ".join(f'"{t}"[dis]' for t in terms)
    return clause


class ClinVarCollector(BaseCollector):
    collection_method = "official_api"

    def __init__(self, source, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(source, http_client)
        settings = get_settings()
        self.base_url = source.base_url or settings.clinvar_base_url
        self.api_key = settings.clinvar_api_key
        self.rate_limiter = RateLimiter(settings.clinvar_rate_limit_per_sec)
        self._owns_client = http_client is None
        self.client = http_client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def collect(self, since: Optional[datetime] = None) -> AsyncIterator[NormalizedRecord]:
        # `since` is not used to filter the query -- ClinVar's esearch
        # does not have a documented, verified date-range field this
        # build has confidence in (same deliberate-non-guess reasoning as
        # app/collectors/clinicaltrials.py's module docstring). Downstream
        # deduplication still classifies an unchanged re-fetched record as
        # DUPLICATE, so this costs extra API calls, never duplicate data.
        config = self.source.configuration or {}
        terms = config.get("disease_terms") or DEFAULT_DISEASE_TERMS
        query = config.get("query_override") or build_query(terms)
        retmax = min(int(config.get("page_size", 200)), 500)

        try:
            uids = await self._esearch(query, retmax=retmax)
            if not uids:
                return
            for batch_start in range(0, len(uids), 50):
                batch = uids[batch_start : batch_start + 50]
                async for record in self._esummary(batch):
                    yield record
        except httpx.HTTPError as exc:
            raise CollectorError(f"ClinVar collection failed: {exc}") from exc
        finally:
            if self._owns_client:
                await self.client.aclose()

    async def _esearch(self, query: str, retmax: int = 200) -> list[str]:
        await self.rate_limiter.wait()
        params = {"db": "clinvar", "term": query, "retmode": "json", "retmax": str(retmax)}
        if self.api_key:
            params["api_key"] = self.api_key
        resp = await self.client.get(ESEARCH_PATH, params=params)
        resp.raise_for_status()
        data = resp.json()
        return data.get("esearchresult", {}).get("idlist", [])

    async def _esummary(self, uids: list[str]) -> AsyncIterator[NormalizedRecord]:
        await self.rate_limiter.wait()
        params = {"db": "clinvar", "id": ",".join(uids), "retmode": "json"}
        if self.api_key:
            params["api_key"] = self.api_key
        resp = await self.client.get(ESUMMARY_PATH, params=params)
        resp.raise_for_status()
        data = resp.json()
        for record in parse_clinvar_summary(data):
            yield record


def parse_clinvar_summary(payload: dict[str, Any]) -> list[NormalizedRecord]:
    """
    Pure parsing function, unit-testable offline against recorded fixture
    JSON (tests/fixtures/clinvar_responses.py) -- same pattern as every
    other collector. `payload` is a full `esummary.fcgi?db=clinvar&
    retmode=json` response: `{"result": {"uids": [...], "<uid>": {...}, ...}}`.
    """
    result = payload.get("result", {})
    uids = result.get("uids", [])
    records: list[NormalizedRecord] = []

    for uid in uids:
        entry = result.get(uid)
        if not entry:
            continue

        accession = entry.get("accession_version") or entry.get("accession")
        title = entry.get("title")
        if not accession or not title:
            continue

        genes = [g.get("symbol") for g in (entry.get("genes") or []) if g.get("symbol")]

        # Germline is the common case for a hereditary condition like
        # CMT; a small minority of ClinVar records instead carry
        # `somatic_classification`/`oncogenicity_classification` (somatic
        # cancer-variant records, not relevant to CMT) -- fall back
        # gracefully to an empty classification rather than raising, so
        # one unusual record never aborts a whole batch.
        classification = entry.get("germline_classification") or {}
        significance = classification.get("description")
        trait_set = classification.get("trait_set") or []
        conditions = [t.get("trait_name") for t in trait_set if t.get("trait_name")]

        review_status = classification.get("review_status")
        last_evaluated = classification.get("last_evaluated")
        pub_date = _parse_clinvar_date(last_evaluated)

        gene_str = ", ".join(genes) if genes else "unknown gene"
        condition_str = "; ".join(conditions) if conditions else "condition not specified"
        synopsis = (
            f"ClinVar germline classification for {gene_str}: {title}. "
            f"Clinical significance: {significance or 'not provided'} "
            f"(review status: {review_status or 'not provided'}). "
            f"Associated condition(s): {condition_str}."
        )

        records.append(
            NormalizedRecord(
                external_id=accession,
                canonical_url=f"https://www.ncbi.nlm.nih.gov/clinvar/{accession}/",
                title=title,
                authors=[],
                publication_date=pub_date,
                description=synopsis,
                raw_metadata={
                    "source": "clinvar",
                    "genes": genes,
                    "clinical_significance": significance,
                    "review_status": review_status,
                    "variant_type": entry.get("obj_type"),
                    # Same key/shape as
                    # app/collectors/clinicaltrials.py's
                    # raw_metadata["conditions"] -- consumed as-is by
                    # app/services/intelligence/eligibility.py's
                    # structured-conditions-first logic, unchanged.
                    "conditions": conditions,
                },
            )
        )
    return records


def _parse_clinvar_date(value: Optional[str]):
    """ClinVar dates look like '2025/10/18 00:00' or '2025/10/18'."""
    if not value:
        return None
    date_part = value.split(" ")[0]
    try:
        return datetime.strptime(date_part, "%Y/%m/%d").date()
    except ValueError:
        return None
