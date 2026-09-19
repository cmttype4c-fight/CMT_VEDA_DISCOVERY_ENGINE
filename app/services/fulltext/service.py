"""
Full-text acquisition orchestration (CMT-specific overhaul, Phase 5/6).

    CMT candidate -> resolve full text -> acquire PDF where available
        -> validate -> SHA-256 hash -> store/reference document
        -> provenance -> mark full_text_available

Idempotent and retryable (spec requirement): `acquire_full_text` can be
called again for the same candidate at any time -- if a
`discovery_documents` row already exists with retrieval_status="acquired"
for this candidate, it short-circuits and returns that row rather than
re-downloading. A prior "unavailable"/"failed" row does not block a retry
(the resolver is queried fresh each time).

A missing/inaccessible PDF is explicitly NOT an error at the candidate
level: `candidate.full_text_available` simply stays False and everything
else about the candidate (classification, editorial, newsletter, RAG
eligibility) proceeds completely normally -- see the docstring on
app.services.fulltext.resolver and app/worker/handlers.py's
handle_resolve_full_text, which always enqueues the next pipeline step
(generate_editorial_draft) regardless of outcome.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.logging_config import get_logger
from app.models.candidate import DiscoveryCandidate
from app.models.document import DiscoveryDocument
from app.services.fulltext.content_sniff import sniff_format
from app.services.fulltext.extraction import extract_text
from app.services.fulltext.resolver import resolve_full_text
from app.services.fulltext.storage import get_document_storage

logger = get_logger(component="fulltext")

_EXT_BY_FORMAT = {"pdf": ".pdf", "xml": ".xml", "html": ".html"}
_MIME_BY_FORMAT = {"pdf": "application/pdf", "xml": "application/xml", "html": "text/html"}


async def acquire_full_text(db: Session, candidate: DiscoveryCandidate) -> DiscoveryDocument:
    existing = db.execute(
        select(DiscoveryDocument)
        .where(DiscoveryDocument.candidate_id == candidate.id, DiscoveryDocument.retrieval_status == "acquired")
        .order_by(DiscoveryDocument.created_at.desc())
    ).scalars().first()
    if existing:
        return existing

    resolution = await resolve_full_text(candidate)

    if not resolution.available:
        doc = DiscoveryDocument(
            candidate_id=candidate.id,
            doi=candidate.doi,
            pmid=candidate.pmid,
            source="europepmc",
            retrieval_status="unavailable",
            error_detail=resolution.reason,
        )
        db.add(doc)
        db.flush()
        return doc

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(resolution.url)
            resp.raise_for_status()
            content = resp.content
    except httpx.HTTPError as exc:
        doc = DiscoveryDocument(
            candidate_id=candidate.id,
            doi=candidate.doi,
            pmid=candidate.pmid,
            source="europepmc",
            full_text_source=resolution.full_text_source,
            document_url=resolution.url,
            retrieval_status="failed",
            error_detail=f"download failed: {exc}",
        )
        db.add(doc)
        db.flush()
        logger.warning("fulltext_download_failed", candidate_id=str(candidate.id), error=str(exc))
        return doc

    if len(content) > settings.fulltext_max_bytes:
        doc = DiscoveryDocument(
            candidate_id=candidate.id,
            doi=candidate.doi,
            pmid=candidate.pmid,
            source="europepmc",
            full_text_source=resolution.full_text_source,
            document_url=resolution.url,
            retrieval_status="failed",
            error_detail=f"file size {len(content)} exceeds fulltext_max_bytes={settings.fulltext_max_bytes}",
        )
        db.add(doc)
        db.flush()
        return doc

    # FINAL CORRECTIVE PROMPT #3: "the system must never label an unknown
    # or mismatched document as PDF... do not use .pdf as a generic
    # fallback for unknown MIME/content." The resolver's claimed
    # `full_text_format` (derived from the source's own metadata, e.g.
    # Europe PMC's `documentStyle`) is NEVER trusted for what actually got
    # downloaded -- the real bytes are sniffed here and are authoritative.
    actual_format = sniff_format(content)
    if actual_format is None:
        doc = DiscoveryDocument(
            candidate_id=candidate.id,
            doi=candidate.doi,
            pmid=candidate.pmid,
            source="europepmc",
            full_text_source=resolution.full_text_source,
            document_url=resolution.url,
            mime_type=None,
            full_text_format=None,
            pdf_available=False,
            retrieval_status="unsupported",
            error_detail=(
                f"downloaded content did not match any recognized full-text format "
                f"(pdf/xml/html); resolver claimed {resolution.full_text_format!r} but the "
                f"actual bytes did not match that or any other known signature -- not stored"
            ),
        )
        db.add(doc)
        db.flush()
        logger.warning(
            "fulltext_content_unsupported", candidate_id=str(candidate.id),
            claimed_format=resolution.full_text_format,
        )
        return doc

    if resolution.full_text_format and actual_format != resolution.full_text_format:
        logger.warning(
            "fulltext_format_mismatch", candidate_id=str(candidate.id),
            claimed_format=resolution.full_text_format, actual_format=actual_format,
        )

    storage = get_document_storage()
    ext = _EXT_BY_FORMAT[actual_format]
    stored = storage.save(content, suggested_ext=ext)

    # Storage-level dedup only (fixed per corrective prompt #10): the
    # PHYSICAL FILE may already be on disk from another candidate that
    # resolved to the same document (`storage.save` is itself
    # content-hash-idempotent and simply returns the existing
    # content_hash/document_ref without rewriting the file). That must
    # NOT be conflated with the candidate-to-document ASSOCIATION --
    # every candidate still gets its own DiscoveryDocument row so it can
    # always be looked up by its own candidate_id, even when it shares
    # physical storage with another candidate's document.
    existing_for_candidate = db.execute(
        select(DiscoveryDocument).where(
            DiscoveryDocument.candidate_id == candidate.id,
            DiscoveryDocument.content_hash == stored.content_hash,
        )
    ).scalars().first()
    if existing_for_candidate:
        candidate.full_text_available = True
        return existing_for_candidate

    # FINAL CORRECTIVE PROMPT #1: extract readable text from the acquired
    # file and store it so editorial generation can actually use it as
    # primary source material (see
    # app/services/intelligence/editorial_service.py) instead of merely
    # storing the file and ignoring it.
    extraction = extract_text(content, actual_format)

    pdf_available = actual_format == "pdf"
    doc = DiscoveryDocument(
        candidate_id=candidate.id,
        doi=candidate.doi,
        pmid=candidate.pmid,
        source="europepmc",
        full_text_source=resolution.full_text_source,
        document_url=resolution.url,
        retrieved_at=datetime.now(timezone.utc),
        mime_type=_MIME_BY_FORMAT[actual_format],
        full_text_format=actual_format,
        pdf_available=pdf_available,
        file_size=stored.file_size,
        content_hash=stored.content_hash,
        document_ref=stored.document_ref,
        license_provenance="Europe PMC open-access full text (isOpenAccess=Y); see document_url for the original source.",
        retrieval_status="acquired",
        extracted_text=extraction.text,
        extracted_char_count=extraction.char_count if extraction.success else None,
        extraction_status="success" if extraction.success else "failed",
        extraction_error=extraction.error,
    )
    db.add(doc)
    db.flush()

    candidate.full_text_available = True
    logger.info(
        "fulltext_acquired", candidate_id=str(candidate.id), document_id=str(doc.id),
        full_text_format=actual_format, pdf_available=pdf_available,
        extraction_status=doc.extraction_status, extracted_chars=doc.extracted_char_count,
    )
    return doc
