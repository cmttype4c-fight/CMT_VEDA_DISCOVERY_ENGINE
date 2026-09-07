"""
ClinicalTrials.gov collector (spec #8) via the official ClinicalTrials.gov
API v2 (Official API -- top collection priority, spec #7).

Status changes (Recruiting -> Active -> Completed) are surfaced through
`raw_metadata["status"]`, which participates in the content hash (see
NormalizedRecord.content_hash) so the deduplication service correctly
classifies a status change as UPDATED rather than DUPLICATE, preserving
source history (spec #12).

INCREMENTAL COLLECTION (spec #35) -- DELIBERATE V1 BEHAVIOR:
`collect()` accepts `since` (required by the shared BaseCollector
interface) but does NOT currently use it to filter or sort the
ClinicalTrials.gov query -- every run re-fetches the full set of studies
matching `query_term` (bounded by `max_records_per_run`), the same as a
historical import. This is a deliberate choice, not an oversight:

  - The PubMed collector (app/collectors/pubmed.py) DOES use `since`,
    via NCBI's `[pdat]` publication-date field syntax -- a
    decades-stable, extensively documented part of PubMed's search
    grammar that this build has high confidence in even without live
    verification.
  - ClinicalTrials.gov API v2 is comparatively newer, and this build
    could not verify against the live service (no network access) either
    the exact Essie field-search syntax for a server-side
    LastUpdatePostDate range filter, or whether a `sort=` parameter
    reliably returns newest-updated-first (which a client-side
    early-termination optimization would need to rely on).
  - Guessing wrong here has a worse failure mode than just being
    inefficient: if an assumed sort order or filter syntax turned out to
    be incorrect, the collector could silently SKIP genuinely
    new/updated trials rather than merely re-fetching unchanged ones --
    a correctness bug, not just a performance one, and one that would be
    very hard to notice at runtime. Given that, `since` is deliberately
    left unused rather than built on an unverified assumption.
  - The inefficiency this causes is bounded: downstream deduplication
    (app/services/deduplication.py) already classifies unchanged
    re-fetched studies as DUPLICATE and does not create new candidates
    or re-run analysis for them, so the cost of not filtering
    server-side is extra ClinicalTrials.gov API calls and parsing, not
    duplicate data or wasted AI analysis.

To properly implement this once the API contract can be verified: either
(a) confirm the exact Essie AREA[LastUpdatePostDate]RANGE[...] (or
equivalent) syntax for `query.term` and add it here, or (b) confirm the
`sort` parameter's exact name/values and reliability, then reinstate the
client-side early-termination approach this file's history shows was
considered and deliberately not shipped unverified. See
IMPLEMENTATION_STATUS.md.

NOTE: requires network access; could not be executed against the live
service from this sandbox -- see IMPLEMENTATION_STATUS.md. Parsing is
unit-tested against fixture JSON (tests/fixtures/clinicaltrials_responses.py).
The deliberate no-op `since` behavior described above is itself locked in
by a regression test (tests/test_clinicaltrials_incremental.py) so a
future change to this behavior must be a conscious, tested one.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, AsyncIterator, Optional

import httpx

from app.collectors.base import BaseCollector, CollectorError, NormalizedRecord, RateLimiter
from app.config import get_settings

STUDIES_PATH = "/studies"

DEFAULT_QUERY_TERM = (
    "Charcot-Marie-Tooth OR CMT OR \"hereditary motor sensory neuropathy\" "
    "OR HMSN OR \"hereditary neuropathy\""
)


class ClinicalTrialsCollector(BaseCollector):
    collection_method = "official_api"

    def __init__(self, source, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(source, http_client)
        settings = get_settings()
        self.base_url = source.base_url or settings.clinicaltrials_base_url
        self.rate_limiter = RateLimiter(settings.clinicaltrials_rate_limit_per_sec)
        self._owns_client = http_client is None
        self.client = http_client or httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def collect(self, since: Optional[datetime] = None) -> AsyncIterator[NormalizedRecord]:
        # `since` is intentionally not used to filter/sort this query --
        # see the module docstring's "INCREMENTAL COLLECTION" section for
        # why this is a deliberate v1 choice, not an oversight.
        config = self.source.configuration or {}
        query_term = config.get("query_override") or DEFAULT_QUERY_TERM
        page_size = min(int(config.get("page_size", 100)), 200)

        try:
            page_token = None
            fetched = 0
            max_records = int(config.get("max_records_per_run", 1000))
            while True:
                await self.rate_limiter.wait()
                params: dict[str, Any] = {
                    "query.term": query_term,
                    "pageSize": page_size,
                    "format": "json",
                }
                if page_token:
                    params["pageToken"] = page_token

                resp = await self.client.get(STUDIES_PATH, params=params)
                resp.raise_for_status()
                data = resp.json()

                for study in data.get("studies", []):
                    record = parse_clinicaltrials_study(study)
                    if record:
                        yield record
                        fetched += 1

                page_token = data.get("nextPageToken")
                if not page_token or fetched >= max_records:
                    break
        except httpx.HTTPError as exc:
            raise CollectorError(f"ClinicalTrials.gov collection failed: {exc}") from exc
        finally:
            if self._owns_client:
                await self.client.aclose()


def parse_clinicaltrials_study(study: dict[str, Any]) -> Optional[NormalizedRecord]:
    """Pure parsing function, unit-testable without network access."""
    protocol = study.get("protocolSection", {})
    identification = protocol.get("identificationModule", {})
    status_module = protocol.get("statusModule", {})
    sponsor_module = protocol.get("sponsorCollaboratorsModule", {})
    description_module = protocol.get("descriptionModule", {})
    contacts_module = protocol.get("contactsLocationsModule", {})

    nct_id = identification.get("nctId")
    if not nct_id:
        return None

    title = identification.get("officialTitle") or identification.get("briefTitle")
    brief_summary = description_module.get("briefSummary")

    lead_sponsor = (sponsor_module.get("leadSponsor") or {}).get("name")

    institutions = []
    for loc in contacts_module.get("locations", []) or []:
        facility = loc.get("facility")
        if facility and facility not in institutions:
            institutions.append(facility)

    overall_status = status_module.get("overallStatus")
    last_update_date = status_module.get("lastUpdatePostDateStruct", {}).get("date")
    start_date = status_module.get("startDateStruct", {}).get("date")

    pub_date = _parse_ct_date(start_date)

    return NormalizedRecord(
        external_id=nct_id,
        canonical_url=f"https://clinicaltrials.gov/study/{nct_id}",
        title=title,
        authors=[],
        institution=institutions[0] if institutions else None,
        publisher=lead_sponsor,
        clinical_trial_id=nct_id,
        publication_date=pub_date,
        description=brief_summary,
        raw_metadata={
            "source": "clinicaltrials.gov",
            "status": overall_status,
            "last_update_posted": last_update_date,
            "all_institutions": institutions,
        },
    )


def _parse_ct_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m"):
        try:
            parsed = datetime.strptime(value, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None
