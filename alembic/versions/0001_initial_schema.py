"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-08

Creates the full Discovery Engine schema (spec #4): discovery_sources,
discovery_candidates, discovery_runs, discovery_source_records,
discovery_analysis, discovery_editorial_drafts, newsletter_items,
newsletter_publications, rag_ingestion_requests, discovery_taxonomy,
discovery_audit_log, discovery_jobs.

Targets PostgreSQL specifically (native UUID + JSONB), per spec #4's
"PostgreSQL-compatible migrations" -- the app layer's SQLite portability
(app/models/base.py) exists for fast local testing via
`Base.metadata.create_all()`, not for Alembic, which always targets real
Postgres in staging/production.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_tier", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(1024), nullable=True),
        sa.Column("collection_method", sa.String(50), nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("frequency", sa.String(50), nullable=False, server_default="daily"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("configuration", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_discovery_sources_type_enabled", "discovery_sources", ["source_type", "enabled"])
    op.create_index("ix_discovery_sources_tier", "discovery_sources", ["source_tier"])

    op.create_table(
        "discovery_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("subtitle", sa.Text, nullable=True),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_date", sa.Date, nullable=True),
        sa.Column("last_source_update", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_sources.id"), nullable=True),
        sa.Column("source_name", sa.String(255), nullable=True),
        sa.Column("source_type", sa.String(50), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("source_tier", sa.String(20), nullable=True),
        sa.Column("authors", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("institution", sa.String(512), nullable=True),
        sa.Column("journal", sa.String(512), nullable=True),
        sa.Column("doi", sa.String(255), nullable=True),
        sa.Column("pmid", sa.String(50), nullable=True),
        sa.Column("clinical_trial_id", sa.String(50), nullable=True),
        sa.Column("publisher", sa.String(512), nullable=True),
        sa.Column("source_reliability", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("scope", sa.String(50), nullable=True),
        sa.Column("cmt_subtypes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("genes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("topics", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("study_type", sa.String(100), nullable=True),
        sa.Column("population", sa.Text, nullable=True),
        sa.Column("intervention", sa.Text, nullable=True),
        sa.Column("comparator", sa.Text, nullable=True),
        sa.Column("outcome", sa.Text, nullable=True),
        sa.Column("key_findings", sa.Text, nullable=True),
        sa.Column("limitations", sa.Text, nullable=True),
        sa.Column("has_manual_override", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("newsletter_status", sa.String(20), nullable=False, server_default="not_selected"),
        sa.Column("rag_status", sa.String(20), nullable=False, server_default="not_selected"),
        sa.Column("abstract", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_candidates_content_type", "discovery_candidates", ["content_type"])
    op.create_index("ix_candidates_scope", "discovery_candidates", ["scope"])
    op.create_index("ix_candidates_discovered_at", "discovery_candidates", ["discovered_at"])
    op.create_index("ix_candidates_newsletter_status", "discovery_candidates", ["newsletter_status"])
    op.create_index("ix_candidates_rag_status", "discovery_candidates", ["rag_status"])
    op.create_index("ix_candidates_doi", "discovery_candidates", ["doi"])
    op.create_index("ix_candidates_pmid", "discovery_candidates", ["pmid"])
    op.create_index("ix_candidates_ctid", "discovery_candidates", ["clinical_trial_id"])

    op.create_table(
        "discovery_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_sources.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_found", sa.Integer, nullable=False, server_default="0"),
        sa.Column("new_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("duplicates", sa.Integer, nullable=False, server_default="0"),
        sa.Column("updated_records", sa.Integer, nullable=False, server_default="0"),
        sa.Column("candidates_created", sa.Integer, nullable=False, server_default="0"),
        sa.Column("errors", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_details", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("run_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "run_key", name="uq_discovery_runs_source_run_key"),
    )
    op.create_index("ix_discovery_runs_source_status", "discovery_runs", ["source_id", "status"])

    op.create_table(
        "discovery_source_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(512), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_sources.id"), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_runs.id"), nullable=True),
        sa.Column("canonical_url", sa.String(2048), nullable=True),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("authors", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("institution", sa.String(512), nullable=True),
        sa.Column("journal", sa.String(512), nullable=True),
        sa.Column("publisher", sa.String(512), nullable=True),
        sa.Column("doi", sa.String(255), nullable=True),
        sa.Column("pmid", sa.String(50), nullable=True),
        sa.Column("clinical_trial_id", sa.String(50), nullable=True),
        sa.Column("publication_date", sa.Date, nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("abstract", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("raw_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("dedupe_status", sa.String(20), nullable=True),
        sa.Column("supersedes_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_candidates.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_source_records_source_external", "discovery_source_records", ["source_id", "external_id"])
    op.create_index("ix_source_records_doi", "discovery_source_records", ["doi"])
    op.create_index("ix_source_records_pmid", "discovery_source_records", ["pmid"])
    op.create_index("ix_source_records_ctid", "discovery_source_records", ["clinical_trial_id"])
    op.create_index("ix_source_records_url", "discovery_source_records", ["canonical_url"])
    op.create_index("ix_source_records_current", "discovery_source_records", ["is_current"])

    op.create_table(
        "discovery_analysis",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_candidates.id"), nullable=False),
        sa.Column("cmt_relevance_score", sa.Integer, nullable=False),
        sa.Column("peripheral_neuropathy_relevance_score", sa.Integer, nullable=False),
        sa.Column("clinical_relevance_score", sa.Integer, nullable=False),
        sa.Column("research_importance_score", sa.Integer, nullable=False),
        sa.Column("patient_relevance_score", sa.Integer, nullable=False),
        sa.Column("analysis_confidence", sa.Integer, nullable=False),
        sa.Column("selection_reason", sa.Text, nullable=True),
        sa.Column("rules_output", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("ai_output", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("proposed_scope", sa.String(50), nullable=True),
        sa.Column("proposed_cmt_subtypes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("proposed_genes", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("proposed_topics", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("analysis_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("taxonomy_version", sa.String(50), nullable=True),
        sa.Column("is_latest", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_analysis_candidate", "discovery_analysis", ["candidate_id"])
    op.create_index("ix_analysis_candidate_latest", "discovery_analysis", ["candidate_id", "is_latest"])

    op.create_table(
        "discovery_editorial_drafts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_candidates.id"), nullable=False),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_analysis.id"), nullable=True),
        sa.Column("headline", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("why_it_matters", sa.Text, nullable=True),
        sa.Column("key_points", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("detailed_content", sa.Text, nullable=True),
        sa.Column("cmt_relevance_explanation", sa.Text, nullable=True),
        sa.Column("limitations", sa.Text, nullable=True),
        sa.Column("references", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("disclaimer", sa.Text, nullable=False),
        sa.Column("draft_status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("draft_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_edited_by", sa.String(255), nullable=True),
        sa.Column("is_ai_generated", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("generation_model", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_editorial_candidate", "discovery_editorial_drafts", ["candidate_id"])
    op.create_index("ix_editorial_candidate_current", "discovery_editorial_drafts", ["candidate_id", "is_current"])

    op.create_table(
        "newsletter_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_candidates.id"), nullable=False, unique=True),
        sa.Column("editorial_draft_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_editorial_drafts.id"), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="not_selected"),
        sa.Column("selected_by", sa.String(255), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(255), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_newsletter_items_status", "newsletter_items", ["status"])

    op.create_table(
        "newsletter_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("item_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("publication_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("published_by", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_newsletter_pub_status", "newsletter_publications", ["status"])

    op.create_table(
        "rag_ingestion_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("discovery_candidates.id"), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="not_selected"),
        sa.Column("source_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("original_source_accessible", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("scientific_relevance_confirmed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("suitable_for_ask_veda", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("content_permitted_for_ingestion", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("verification_notes", sa.Text, nullable=True),
        sa.Column("verified_by", sa.String(255), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(255), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text, nullable=True),
        sa.Column("ingestion_metadata", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("knowledge_version", sa.String(50), nullable=True),
        sa.Column("external_ingestion_id", sa.String(255), nullable=True),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rag_requests_status", "rag_ingestion_requests", ["status"])

    op.create_table(
        "discovery_taxonomy",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("term", sa.String(255), nullable=False),
        sa.Column("synonyms", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("taxonomy_version", sa.String(50), nullable=False, server_default="v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("category", "term", name="uq_taxonomy_category_term"),
    )
    op.create_index("ix_taxonomy_category", "discovery_taxonomy", ["category"])

    op.create_table(
        "discovery_audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("performed_by", sa.String(255), nullable=False),
        sa.Column("performed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("old_value", postgresql.JSONB, nullable=True),
        sa.Column("new_value", postgresql.JSONB, nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
    )
    op.create_index("ix_audit_candidate_id", "discovery_audit_log", ["candidate_id"])
    op.create_index("ix_audit_candidate_time", "discovery_audit_log", ["candidate_id", "performed_at"])
    op.create_index("ix_audit_action", "discovery_audit_log", ["action"])

    op.create_table(
        "discovery_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("checkpoint", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_status_available", "discovery_jobs", ["status", "available_at"])
    op.create_index("ix_jobs_type_status", "discovery_jobs", ["job_type", "status"])
    op.create_index("ix_jobs_dedupe_key", "discovery_jobs", ["dedupe_key"])
    # Concurrency-safe idempotency (spec #34, #49): the previous revision
    # of this migration only had the plain index above, matching the
    # docstring's claim of DB-level protection in name only -- two
    # simultaneous "run now" (or any other dedupe_key'd enqueue) calls
    # could each pass the application's check-then-insert and both
    # succeed, creating two active jobs for the same dedupe_key. This
    # partial unique index makes that impossible at the database level:
    # at most one row with a given dedupe_key may have status 'queued' or
    # 'running' at a time. Multiple historical (completed/failed/
    # dead_letter) rows, and multiple NULL dedupe_keys, remain unaffected.
    # app/worker/job_queue.py::enqueue() catches the resulting
    # IntegrityError on a lost race and returns the winning row instead
    # of raising.
    op.create_index(
        "uq_jobs_dedupe_key_active",
        "discovery_jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_table("discovery_jobs")
    op.drop_table("discovery_audit_log")
    op.drop_table("discovery_taxonomy")
    op.drop_table("rag_ingestion_requests")
    op.drop_table("newsletter_publications")
    op.drop_table("newsletter_items")
    op.drop_table("discovery_editorial_drafts")
    op.drop_table("discovery_analysis")
    op.drop_table("discovery_source_records")
    op.drop_table("discovery_runs")
    op.drop_table("discovery_candidates")
    op.drop_table("discovery_sources")
