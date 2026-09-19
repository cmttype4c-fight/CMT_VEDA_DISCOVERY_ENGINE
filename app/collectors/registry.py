"""
Collector registry (spec #5, #8): maps a source's `collection_method` to
the collector class that handles it. Adding a new source of an
already-supported collection method (e.g. another RSS-based org) requires
zero code changes -- just a new discovery_sources row. Adding a genuinely
new *method* means adding one collector class and one registry entry.
"""
from __future__ import annotations

from typing import Type

from app.collectors.base import BaseCollector, CollectorError
from app.collectors.clinicaltrials import ClinicalTrialsCollector
from app.collectors.clinvar import ClinVarCollector
from app.collectors.europepmc import EuropePMCCollector
from app.collectors.generic_rss import GenericRSSCollector
from app.collectors.pubmed import PubMedCollector
from app.collectors.wordpress_json import WordPressJSONCollector

# Sources are matched first by an explicit `configuration["collector"]`
# override (for when one collection_method has multiple possible
# implementations, e.g. "official_api" could be PubMed or ClinicalTrials),
# falling back to a collection_method default.
_BY_NAME: dict[str, Type[BaseCollector]] = {
    "pubmed": PubMedCollector,
    "clinicaltrials": ClinicalTrialsCollector,
    "generic_rss": GenericRSSCollector,
    # CMT-specific overhaul, Phase 3A -- registered but not activated by
    # this pass (no discovery_sources row created for it): see
    # app/collectors/europepmc.py's confidence caveat.
    "europepmc": EuropePMCCollector,
    # FINAL FOCUSED CORRECTION, TASK 5 -- both live-verified this pass
    # (see each module's docstring). Registered here so the engine is
    # code-ready; activating a source still requires a real
    # `discovery_sources` row (a deliberate operational decision this
    # implementation pass does not make on the operator's behalf).
    "clinvar": ClinVarCollector,
    "wordpress_json": WordPressJSONCollector,
}

_BY_COLLECTION_METHOD: dict[str, Type[BaseCollector]] = {
    "rss_atom": GenericRSSCollector,
    # "official_api" has no safe default -- it must be named explicitly,
    # since PubMed and ClinicalTrials.gov have very different APIs.
    # "structured_feed" and "controlled_webpage_extraction" are
    # intentionally unregistered for v1 (spec #8: deferred, not
    # production-live yet) -- see NotImplementedCollector below.
}


class NotImplementedCollector(BaseCollector):
    """
    Placeholder for source types the architecture supports but that don't
    have a production-live implementation yet (spec #8): NIH/NINDS,
    RDCRN/Inherited Neuropathies Consortium, controlled webpage extraction
    for orgs without a feed, journal-specific scrapers, etc.

    Raising here (rather than silently no-op'ing) ensures a misconfigured
    source fails loudly on `discovery_runs` instead of quietly collecting
    nothing forever.
    """

    collection_method = "unimplemented"

    async def collect(self, since=None):
        raise CollectorError(
            f"No collector implementation registered for source "
            f"'{self.source.source_name}' (collector="
            f"{self.source.configuration.get('collector')!r}, "
            f"collection_method={self.source.collection_method!r}). "
            f"This source type is deferred -- see IMPLEMENTATION_STATUS.md."
        )
        yield  # pragma: no cover


def get_collector_class(source) -> Type[BaseCollector]:
    explicit = (source.configuration or {}).get("collector")
    if explicit and explicit in _BY_NAME:
        return _BY_NAME[explicit]
    if source.collection_method in _BY_COLLECTION_METHOD:
        return _BY_COLLECTION_METHOD[source.collection_method]
    return NotImplementedCollector


def build_collector(source, http_client=None) -> BaseCollector:
    collector_cls = get_collector_class(source)
    return collector_cls(source, http_client=http_client)
