"""
Normalization service (spec #10).

Takes whatever a collector produced (a NormalizedRecord -- already in the
common shape by construction, see app/collectors/base.py) and turns it
into a `DiscoverySourceRecord` row ready for deduplication. This is kept
as its own step (rather than inlined in the worker) so manual submissions
(spec #27) can be normalized through the exact same code path as
collector output.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.collectors.base import NormalizedRecord
from app.models.source_record import DiscoverySourceRecord


def normalize_to_source_record(
    record: NormalizedRecord,
    *,
    source_id: uuid.UUID,
    source_type: str,
    run_id: uuid.UUID | None = None,
) -> DiscoverySourceRecord:
    now = datetime.now(timezone.utc)
    return DiscoverySourceRecord(
        external_id=record.external_id,
        source_id=source_id,
        source_type=source_type,
        run_id=run_id,
        canonical_url=record.canonical_url,
        title=record.title,
        authors=record.authors or [],
        institution=record.institution,
        journal=record.journal,
        publisher=record.publisher,
        doi=_clean_doi(record.doi),
        pmid=record.pmid,
        clinical_trial_id=record.clinical_trial_id,
        publication_date=record.publication_date,
        first_seen_at=now,
        last_seen_at=now,
        abstract=record.abstract,
        description=record.description,
        raw_metadata=record.raw_metadata or {},
        content_hash=record.content_hash(),
        is_current=True,
    )


def _clean_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    doi = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:", "DOI:"):
        if doi.lower().startswith(prefix.lower()):
            doi = doi[len(prefix):]
            break
    return doi.strip() or None
