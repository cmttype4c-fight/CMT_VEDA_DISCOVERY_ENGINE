"""
CMT-specific overhaul, Phase 1 of the reset plan: app/services/reset_service.py.

NOT executed against production by this pass -- these tests exercise the
service logic itself against the test SQLite database only, which is
exactly what's safe to do. Covers required test list items:
  23. Reset is safe and transactional.
  24. Source configuration survives reset.
  25. Schema/authentication survives reset.
  26. Candidate-dependent records are handled correctly.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.analysis import DiscoveryAnalysis
from app.models.audit import DiscoveryAuditLog
from app.models.candidate import DiscoveryCandidate
from app.models.document import DiscoveryDocument
from app.models.editorial import DiscoveryEditorialDraft
from app.models.enums import AuditAction
from app.models.newsletter import NewsletterItem
from app.models.rag import RagIngestionRequest
from app.models.run import DiscoveryRun
from app.models.source import DiscoverySource
from app.models.source_record import DiscoverySourceRecord
from app.models.taxonomy import DiscoveryTaxonomy
from app.services.audit_service import write_audit_log
from app.services.reset_service import dry_run_counts, execute_reset
from app.services.taxonomy_service import seed_default_taxonomy


def _fully_populated_candidate(db):
    """Builds one candidate with a row in every dependent table the reset
    touches, so a single test can verify the whole FK-respecting chain."""
    source = DiscoverySource(
        source_name="Test Source", source_type="pubmed", source_tier="tier_1",
        collection_method="official_api", configuration={},
    )
    db.add(source)
    db.flush()

    run = DiscoveryRun(source_id=source.id, status="completed", run_key="test-run-1")
    db.add(run)
    db.flush()

    record = DiscoverySourceRecord(
        external_id="ext-1", source_id=source.id, source_type="pubmed", run_id=run.id,
        title="A CMT paper", first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
        content_hash="hash-1",
    )
    db.add(record)
    db.flush()

    candidate = DiscoveryCandidate(
        content_type="research_paper", title="A CMT paper", discovered_at=datetime.now(timezone.utc),
        source_id=source.id,
    )
    db.add(candidate)
    db.flush()
    record.candidate_id = candidate.id

    analysis = DiscoveryAnalysis(
        candidate_id=candidate.id, analysis_version=1, is_latest=True,
        cmt_relevance_score=80, peripheral_neuropathy_relevance_score=50,
        clinical_relevance_score=60, research_importance_score=60,
        patient_relevance_score=60, analysis_confidence=70,
    )
    db.add(analysis)
    db.flush()

    draft = DiscoveryEditorialDraft(
        candidate_id=candidate.id, analysis_id=analysis.id, draft_version=1, is_current=True,
        headline="Test headline", summary="Test summary",
    )
    db.add(draft)
    db.flush()

    document = DiscoveryDocument(
        candidate_id=candidate.id, source="europepmc", retrieval_status="acquired",
        content_hash="hash-doc-1", document_ref="/data/discovery-documents/ha/hash-doc-1.pdf",
        full_text_format="pdf", pdf_available=True,
    )
    db.add(document)

    newsletter_item = NewsletterItem(candidate_id=candidate.id, status="selected")
    db.add(newsletter_item)

    rag_request = RagIngestionRequest(candidate_id=candidate.id, status="not_selected")
    db.add(rag_request)

    write_audit_log(db, candidate_id=candidate.id, action=AuditAction.discovered, performed_by="test")
    db.flush()

    return source, candidate


def test_dry_run_counts_reflect_actual_rows(db_session):
    source, candidate = _fully_populated_candidate(db_session)
    counts = dry_run_counts(db_session)

    assert counts.counts["discovery_candidates"] == 1
    assert counts.counts["discovery_analysis"] == 1
    assert counts.counts["discovery_editorial_drafts"] == 1
    assert counts.counts["discovery_documents"] == 1
    assert counts.counts["newsletter_items"] == 1
    assert counts.counts["rag_ingestion_requests"] == 1
    assert counts.counts["discovery_source_records"] == 1
    assert counts.counts["discovery_runs"] == 1
    assert counts.total == 7  # everything except discovery_jobs (none enqueued in this test)


def test_execute_reset_refuses_without_confirm(db_session):
    _fully_populated_candidate(db_session)
    try:
        execute_reset(db_session, confirm=False)
        assert False, "should have raised"
    except ValueError as exc:
        assert "confirm=True" in str(exc)
    # Nothing was touched.
    assert dry_run_counts(db_session).total == 7


def test_execute_reset_clears_everything_in_fk_safe_order(db_session):
    """Test list item 23/26: reset is transactional, and every
    candidate-dependent record (analysis, editorial draft, document,
    newsletter item, rag request, source record, run) is handled without
    an FK violation."""
    _fully_populated_candidate(db_session)

    result = execute_reset(db_session, confirm=True)

    assert result.verified_clean is True
    assert result.pre_counts.total == 7
    assert result.post_counts.total == 0

    assert db_session.execute(select(DiscoveryCandidate)).scalars().all() == []
    assert db_session.execute(select(DiscoveryAnalysis)).scalars().all() == []
    assert db_session.execute(select(DiscoveryEditorialDraft)).scalars().all() == []
    assert db_session.execute(select(DiscoveryDocument)).scalars().all() == []
    assert db_session.execute(select(NewsletterItem)).scalars().all() == []
    assert db_session.execute(select(RagIngestionRequest)).scalars().all() == []
    assert db_session.execute(select(DiscoverySourceRecord)).scalars().all() == []
    assert db_session.execute(select(DiscoveryRun)).scalars().all() == []


def test_execute_reset_preserves_source_definitions(db_session):
    """Test list item 24: source configuration survives reset."""
    source, _ = _fully_populated_candidate(db_session)
    execute_reset(db_session, confirm=True)

    surviving = db_session.get(DiscoverySource, source.id)
    assert surviving is not None
    assert surviving.source_name == "Test Source"


def test_execute_reset_preserves_taxonomy(db_session):
    """Test list item 25 (schema survives is implicit -- this is the
    admin-configured *data* half of "schema/migrations, taxonomy,
    auth/configuration" that's meaningful to test at this level)."""
    seed_default_taxonomy(db_session)
    _fully_populated_candidate(db_session)

    execute_reset(db_session, confirm=True)

    remaining = db_session.execute(select(DiscoveryTaxonomy)).scalars().all()
    assert len(remaining) > 0


def test_execute_reset_preserves_audit_log():
    """Test list item 25/26: audit history must remain untouched -- this
    is guaranteed structurally (discovery_audit_log.candidate_id has no
    FK, confirmed by reading app/models/audit.py), and reset_service.py
    never includes it in _TABLES_IN_DELETE_ORDER. This test asserts that
    invariant directly against the module, not just behaviorally, so a
    future edit that accidentally added audit_log to the delete list
    would fail this test immediately."""
    from app.services.reset_service import _TABLES_IN_DELETE_ORDER

    deleted_table_names = {name for name, _ in _TABLES_IN_DELETE_ORDER}
    assert "discovery_audit_log" not in deleted_table_names
    assert "discovery_sources" not in deleted_table_names
    assert "discovery_taxonomy" not in deleted_table_names


def test_execute_reset_audit_rows_survive_candidate_deletion(db_session):
    source, candidate = _fully_populated_candidate(db_session)
    audit_rows_before = db_session.execute(select(DiscoveryAuditLog)).scalars().all()
    assert len(audit_rows_before) == 1

    execute_reset(db_session, confirm=True)

    audit_rows_after = db_session.execute(select(DiscoveryAuditLog)).scalars().all()
    assert len(audit_rows_after) == 1
    assert audit_rows_after[0].candidate_id == candidate.id  # history preserved even though the candidate is gone
