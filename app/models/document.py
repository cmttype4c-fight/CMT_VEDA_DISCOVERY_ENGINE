import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class DiscoveryDocument(Base, TimestampMixin):
    """
    Full-text document metadata (CMT-specific overhaul, Phase 5/6).

    Stores only metadata + a storage reference (`document_ref`) -- never
    the PDF binary itself, per explicit instruction not to store large
    PDF binaries directly in PostgreSQL. `document_ref` is a path/URI into
    whatever storage abstraction the deployment uses (local volume path in
    this build's default implementation -- see
    app/services/fulltext/storage.py; swappable for object storage later
    without a schema change since this column is just a reference string).

    One candidate may have zero (no accessible full text -- not an error,
    just full_text_available=False) or one current document. Re-resolution
    (e.g. a retry, or a source update) creates a new row rather than
    mutating in place, preserving acquisition history/provenance; only the
    most recent successful row per candidate is treated as "current" by
    the resolver (no separate is_current flag needed at v1 since a
    candidate practically has at most one realistic full-text source, but
    `content_hash` still gives a hard dedup key so the same physical PDF
    is never stored twice even across candidates, e.g. a preprint and its
    published version resolving to the same file).

    IMPORTANT (fixed per corrective prompt #10 -- "fix the existing
    cross-candidate deduplication issue"): physical file storage is
    content-hash-deduplicated (app/services/fulltext/storage.py never
    writes the same bytes twice), but that is a STORAGE-layer optimization
    only. Every candidate that legitimately resolves to the same
    underlying document (e.g. a preprint and its later-published version,
    or two candidates independently citing the same paper) gets its OWN
    `discovery_documents` row, with its own `candidate_id`, `id`, and
    provenance fields -- content_hash and document_ref may be identical
    across those rows (same physical bytes, same storage path), but the
    candidate-to-document association is never shared or collapsed. The
    unique constraint is therefore on (candidate_id, content_hash), not
    content_hash alone: it prevents the SAME candidate from accumulating
    duplicate rows for the SAME physical file on repeated retries, while
    still allowing different candidates to each have their own row
    pointing at that file.
    """

    __tablename__ = "discovery_documents"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("discovery_candidates.id"), nullable=False)

    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Where the full text was resolved from, e.g. "europepmc_oa", "pmc".
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    full_text_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # "pdf" | "xml" | "html" | "other" -- always the ACTUAL format
    # obtained (corrective prompt #6: "do not pretend HTML/XML is a
    # PDF. Record the actual format accurately.").
    full_text_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pdf_available: Mapped[bool] = mapped_column(default=False, nullable=False)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # SHA-256 hex digest of the acquired file. Shared across candidates
    # that resolve to the identical physical file (see class docstring) --
    # deliberately NOT globally unique; storage-level dedup is enforced in
    # app/services/fulltext/storage.py instead.
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    document_ref: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    license_provenance: Mapped[str | None] = mapped_column(Text, nullable=True)

    # queued | resolving | acquired | unavailable | unsupported | failed
    # "unsupported" (FINAL CORRECTIVE PROMPT #3) is distinct from
    # "failed": the download succeeded, but the actual bytes -- verified
    # by app/services/fulltext/content_sniff.py, never trusted from a
    # claimed format -- didn't match any of PDF/XML/HTML. Never stored,
    # never labeled as a PDF.
    retrieval_status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extracted plain-text article body (CMT-specific overhaul, FINAL
    # CORRECTIVE PROMPT #1 -- "full text must actually feed Gemini
    # editorial generation"). Populated by
    # app/services/fulltext/extraction.py at acquisition time; consumed by
    # app/services/intelligence/editorial_service.py as the PRIMARY
    # editorial-generation source material when present. This is
    # source-derived text (extracted verbatim from the acquired
    # PDF/XML/HTML), never AI-generated -- kept clearly distinct from
    # DiscoveryEditorialDraft, which holds the AI-written output derived
    # FROM this text.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_char_count: Mapped[int | None] = mapped_column(nullable=True)
    # not_attempted | success | failed
    extraction_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_attempted")
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_metadata: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)

    candidate = relationship("DiscoveryCandidate")

    __table_args__ = (
        Index("ix_documents_candidate_id", "candidate_id"),
        Index("ix_documents_retrieval_status", "retrieval_status"),
        Index("ix_documents_content_hash", "content_hash"),
        UniqueConstraint("candidate_id", "content_hash", name="uq_documents_candidate_content_hash"),
    )
