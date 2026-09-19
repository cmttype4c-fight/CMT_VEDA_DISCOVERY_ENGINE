"""
Full-text acquisition orchestration (CMT-specific overhaul, Phase 5/6;
EUROPE PMC FULL-TEXT CORRECTION pass: PDF -> official XML -> HTML
fallback chain, so a blocked/failed PDF download is no longer a
terminal candidate failure when XML full text is genuinely available).

    CMT candidate -> resolve full text (ordered PDF/XML/HTML candidate
        chain) -> walk the chain, accepting the first candidate that
        downloads and sniffs as a recognized format -> validate
        -> SHA-256 hash -> store/reference document -> provenance
        -> mark full_text_available

Idempotent and retryable (spec requirement): `acquire_full_text` can be
called again for the same candidate at any time -- if a
`discovery_documents` row already exists with retrieval_status="acquired"
for this candidate, it short-circuits and returns that row rather than
re-downloading. A prior "unavailable"/"failed"/"unsupported" row does not
block a retry (the resolver is queried fresh each time).

A missing/inaccessible PDF is explicitly NOT an error at the candidate
level: `candidate.full_text_available` simply stays False and everything
else about the candidate (classification, editorial, newsletter, RAG
eligibility) proceeds completely normally -- see the docstring on
app.services.fulltext.resolver and app/worker/handlers.py's
handle_resolve_full_text, which always enqueues the next pipeline step
(generate_editorial_draft) regardless of outcome.

CANDIDATE-CHAIN WALK (this pass): `resolve_full_text` now returns an
ORDERED list of candidates (PDF, then the official Europe PMC
`fullTextXML` endpoint, then HTML, then any other listed URL --
app/services/fulltext/resolver.py's `_build_candidates`). `_acquire_from_candidates`
below tries each in turn:
  - a transport/HTTP-layer failure (e.g. the PDF render URL's 403,
    confirmed this pass against real VPS testing of four live PMC
    articles) does NOT terminally fail acquisition -- it falls through
    to the next candidate in the chain;
  - a download that succeeds but whose bytes don't match ANY recognized
    format (`sniff_format` returns None) is treated the same way -- try
    the next candidate rather than immediately giving up, since an
    unrecognized response is exactly what a blocked/interstitial page
    looks like;
  - a download whose bytes DO match a recognized format, but not the one
    Europe PMC's own metadata claimed (e.g. claimed "pdf" but the bytes
    are genuinely HTML), is still ACCEPTED and CORRECTED to the real
    format -- this is FINAL CORRECTIVE PROMPT #3/#6's existing,
    unchanged behavior ("never mislabel", not "never accept a
    mismatch"), so a genuinely-served alternate format is not thrown
    away just because it didn't match the claim.
Only once every candidate in the chain has been exhausted does
acquisition terminally fail (`retrieval_status` "failed" if no candidate
ever produced bytes at all, "unsupported" if at least one did but none
were recognized) -- and, per this correction, format/`pdf_available` are
always recorded from whichever candidate actually succeeded: a
successful XML acquisition stores `full_text_format="xml"`,
`pdf_available=False`, and `full_text_available=True` on the candidate,
never silently downgraded to an abstract-only outcome.
"""
from __future__ import annotations

from dataclasses import dataclass
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
from app.services.fulltext.resolver import FullTextCandidate, ResolutionResult, resolve_full_text
from app.services.fulltext.storage import get_document_storage

logger = get_logger(component="fulltext")

_EXT_BY_FORMAT = {"pdf": ".pdf", "xml": ".xml", "html": ".html"}
_MIME_BY_FORMAT = {"pdf": "application/pdf", "xml": "application/xml", "html": "text/html"}


@dataclass
class _ChainOutcome:
    """Result of walking a resolution's candidate chain: either a
    successful (candidate, content, actual_sniffed_format) triple, or
    None with a list of per-attempt failure descriptions for a terminal
    error message."""

    chosen: FullTextCandidate | None
    content: bytes | None
    actual_format: str | None
    any_bytes_downloaded: bool
    attempt_errors: list[str]


async def _walk_candidate_chain(resolution: ResolutionResult, *, max_bytes: int) -> _ChainOutcome:
    candidates = resolution.candidates or (
        [FullTextCandidate(resolution.url, resolution.mime_type, resolution.full_text_format or "other", resolution.full_text_source or "europepmc_oa")]
        if resolution.url
        else []
    )

    attempt_errors: list[str] = []
    any_bytes_downloaded = False

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for candidate in candidates:
            try:
                resp = await client.get(candidate.url)
                resp.raise_for_status()
                content = resp.content
            except httpx.HTTPError as exc:
                attempt_errors.append(f"{candidate.full_text_format} ({candidate.full_text_source}): download failed: {exc}")
                logger.warning(
                    "fulltext_candidate_download_failed",
                    claimed_format=candidate.full_text_format,
                    source=candidate.full_text_source,
                    error=str(exc),
                )
                continue

            any_bytes_downloaded = True

            if len(content) > max_bytes:
                attempt_errors.append(
                    f"{candidate.full_text_format} ({candidate.full_text_source}): "
                    f"size {len(content)} exceeds fulltext_max_bytes={max_bytes}"
                )
                continue

            actual_format = sniff_format(content)
            if actual_format is None:
                attempt_errors.append(
                    f"{candidate.full_text_format} ({candidate.full_text_source}): "
                    f"downloaded content did not match any recognized full-text format (pdf/xml/html)"
                )
                logger.warning(
                    "fulltext_candidate_unrecognized",
                    claimed_format=candidate.full_text_format,
                    source=candidate.full_text_source,
                )
                continue

            if actual_format != candidate.full_text_format:
                # Same "never mislabel, but don't discard a genuinely
                # different valid format" behavior as before this pass
                # (FINAL CORRECTIVE PROMPT #3/#6) -- accepted, not
                # treated as a failed attempt.
                logger.warning(
                    "fulltext_format_mismatch",
                    claimed_format=candidate.full_text_format,
                    actual_format=actual_format,
                    source=candidate.full_text_source,
                )

            return _ChainOutcome(candidate, content, actual_format, any_bytes_downloaded, attempt_errors)

    return _ChainOutcome(None, None, None, any_bytes_downloaded, attempt_errors)


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

    # EUROPE PMC FULL-TEXT CORRECTION (this pass): walk the PDF -> XML ->
    # HTML candidate chain instead of attempting a single URL. A PDF
    # download that fails at the HTTP layer (e.g. the 403 confirmed this
    # pass on Europe PMC's PDF render URL from real VPS testing) is NOT a
    # terminal failure here -- it simply moves on to the next candidate
    # (the official fullTextXML endpoint, then HTML) -- see
    # _walk_candidate_chain's docstring above for the full accept/reject
    # rules, unchanged from FINAL CORRECTIVE PROMPT #3/#6 in every other
    # respect (a genuinely different-but-recognized format is still
    # accepted and corrected, never discarded; an entirely unrecognized
    # response is still never stored/labeled as a known format).
    outcome = await _walk_candidate_chain(resolution, max_bytes=settings.fulltext_max_bytes)

    if outcome.chosen is None:
        # Every candidate in the chain either failed to download or
        # produced unrecognized content. "failed" (no candidate ever
        # returned bytes at all -- e.g. every URL was blocked/erroring)
        # is kept distinct from "unsupported" (at least one candidate
        # downloaded successfully but its content didn't match any
        # known format), preserving the same two-state distinction this
        # module has always made, now applied across the whole chain
        # rather than a single attempt.
        last_candidate = resolution.candidates[-1] if resolution.candidates else None
        status = "unsupported" if outcome.any_bytes_downloaded else "failed"
        summary = "; ".join(outcome.attempt_errors) if outcome.attempt_errors else "no full-text candidates were available"
        doc = DiscoveryDocument(
            candidate_id=candidate.id,
            doi=candidate.doi,
            pmid=candidate.pmid,
            source="europepmc",
            full_text_source=last_candidate.full_text_source if last_candidate else resolution.full_text_source,
            document_url=last_candidate.url if last_candidate else resolution.url,
            mime_type=None,
            full_text_format=None,
            pdf_available=False,
            retrieval_status=status,
            error_detail=(
                f"no candidate in the PDF/XML/HTML fallback chain could be retrieved "
                f"(not stored, none matched a recognized format): {summary}"
            ),
        )
        db.add(doc)
        db.flush()
        logger.warning(
            "fulltext_all_candidates_failed", candidate_id=str(candidate.id),
            attempts=len(resolution.candidates), status=status,
        )
        return doc

    chosen = outcome.chosen
    content = outcome.content
    actual_format = outcome.actual_format

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
        full_text_source=chosen.full_text_source,
        document_url=chosen.url,
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
