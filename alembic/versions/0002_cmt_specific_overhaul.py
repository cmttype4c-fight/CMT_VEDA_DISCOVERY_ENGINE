"""cmt-specific overhaul: eligibility gate + full-text acquisition columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-19

CMT-specific overhaul, Phase 2/5/6. Entirely additive -- no column is
renamed or dropped, every new column is nullable or has a safe
server_default, and no existing table's semantics change. Does NOT touch
discovery_sources, discovery_taxonomy, discovery_audit_log, or any
auth/newsletter/RAG-state-machine schema.

Changes:
  - discovery_source_records: + cmt_eligible, eligibility_reason
    (set by the new CMT eligibility gate at collection time -- Phase 2C)
  - discovery_runs: + cmt_rejected (per-run count of records the
    eligibility gate rejected -- Phase 10 observability)
  - discovery_candidates: + full_text_available (denormalized flag,
    same pattern as newsletter_status/rag_status -- Phase 5/6)
  - new table discovery_documents (full-text/PDF metadata -- Phase 5/6;
    never stores the PDF binary itself, only a storage reference)

Taxonomy expansion (Phase 1/2E) is data, not schema, and is handled by
app/services/taxonomy_service.py's idempotent seed function -- no
migration needed for that part.

UPDATED (FINAL CORRECTIVE PROMPT, before this migration was ever applied
to any real database -- editing 0002 in place rather than adding 0003,
since nothing has deployed it yet):
  - discovery_documents: + full_text_format ("pdf"/"xml"/"html"/"other",
    corrective prompt #5/#6 -- PDF-first with truthful XML/HTML fallback)
  - discovery_documents: + pdf_available (explicit flag distinct from
    full_text_available, since a candidate can have full text without a
    PDF specifically -- corrective prompt #6)
  - discovery_documents: the unique constraint changed from
    content_hash alone to (candidate_id, content_hash) -- fixes the
    cross-candidate dedup bug from the corrective prompt (#10): the old
    constraint made it IMPOSSIBLE for two different candidates to each
    have their own document row for the same physical file, so the
    acquisition service was silently handing candidate B a reference to
    candidate A's document row instead. Physical-file dedup is enforced
    at the storage layer (app/services/fulltext/storage.py), not by a
    single-column DB constraint.

UPDATED AGAIN (FINAL CORRECTION BEFORE DEPLOYMENT -- still edited in
place, still unapplied to any real database):
  - discovery_documents: + extracted_text, extracted_char_count,
    extraction_status, extraction_error -- corrective prompt #1: "full
    text must actually feed Gemini editorial generation." Populated by
    app/services/fulltext/extraction.py at acquisition time.
  - discovery_documents.retrieval_status gains a new value, "unsupported"
    (existing column, no schema change needed) -- corrective prompt #3:
    content whose bytes don't match any recognized format (verified by
    app/services/fulltext/content_sniff.py) is never stored or labeled
    as a PDF.
  - discovery_editorial_drafts: + source_document_id (FK to
    discovery_documents.id, nullable), + used_full_text -- provenance of
    whether a given draft's generation was given the actual extracted
    source full text or only the abstract.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("discovery_source_records", sa.Column("cmt_eligible", sa.Boolean, nullable=True))
    op.add_column("discovery_source_records", sa.Column("eligibility_reason", sa.Text, nullable=True))

    op.add_column(
        "discovery_runs",
        sa.Column("cmt_rejected", sa.Integer, nullable=False, server_default="0"),
    )

    op.add_column(
        "discovery_candidates",
        sa.Column("full_text_available", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "discovery_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discovery_candidates.id"),
            nullable=False,
        ),
        sa.Column("doi", sa.String(255), nullable=True),
        sa.Column("pmid", sa.String(50), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("full_text_source", sa.String(255), nullable=True),
        sa.Column("document_url", sa.String(2048), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("full_text_format", sa.String(20), nullable=True),
        sa.Column("pdf_available", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("file_size", sa.BigInteger, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("document_ref", sa.String(2048), nullable=True),
        sa.Column("license_provenance", sa.Text, nullable=True),
        sa.Column("retrieval_status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("extracted_text", sa.Text, nullable=True),
        sa.Column("extracted_char_count", sa.Integer, nullable=True),
        sa.Column("extraction_status", sa.String(20), nullable=False, server_default="not_attempted"),
        sa.Column("extraction_error", sa.Text, nullable=True),
        sa.Column("raw_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("candidate_id", "content_hash", name="uq_documents_candidate_content_hash"),
    )
    op.create_index("ix_documents_candidate_id", "discovery_documents", ["candidate_id"])
    op.create_index("ix_documents_retrieval_status", "discovery_documents", ["retrieval_status"])
    op.create_index("ix_documents_content_hash", "discovery_documents", ["content_hash"])

    op.add_column(
        "discovery_editorial_drafts",
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_documents.id"), nullable=True),
    )
    op.add_column(
        "discovery_editorial_drafts",
        sa.Column("used_full_text", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("discovery_editorial_drafts", "used_full_text")
    op.drop_column("discovery_editorial_drafts", "source_document_id")
    op.drop_table("discovery_documents")
    op.drop_column("discovery_candidates", "full_text_available")
    op.drop_column("discovery_runs", "cmt_rejected")
    op.drop_column("discovery_source_records", "eligibility_reason")
    op.drop_column("discovery_source_records", "cmt_eligible")
