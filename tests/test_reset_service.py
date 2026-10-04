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
import uuid
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
from app.services.reset_service import (
    _SELECTIVE_TABLES_IN_DELETE_ORDER,
    dry_run_counts,
    dry_run_selective_counts,
    execute_reset,
    execute_selective_reset,
)
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


# ===========================================================================
# Selective reset: "clean the candidate dataset, keep one candidate"
# (dry_run_selective_counts / execute_selective_reset), added for the
# "Simplify Discovery Newsletter Workflow and Clean Dataset" task.
# ===========================================================================


def test_dry_run_selective_counts_reflects_delete_and_retain_split(db_session):
    _, keep_candidate = _fully_populated_candidate(db_session)
    _, other_candidate = _fully_populated_candidate(db_session)

    counts = dry_run_selective_counts(db_session, keep_candidate.id)

    assert counts.retained_candidate_exists is True
    assert counts.retain_candidate_id == keep_candidate.id

    # One fully-populated candidate's worth of rows in each table should
    # be marked for deletion (the "other" one) and one retained (the kept
    # one) -- for every table this operation touches.
    for table in (
        "newsletter_items", "rag_ingestion_requests", "discovery_editorial_drafts",
        "discovery_analysis", "discovery_documents", "discovery_source_records",
        "discovery_candidates",
    ):
        assert counts.deleted[table] == 1, f"{table} deleted count"
        assert counts.retained[table] == 1, f"{table} retained count"

    assert counts.total_to_delete == 7
    assert other_candidate.id != keep_candidate.id  # sanity on the fixture itself


def test_execute_selective_reset_refuses_without_confirm(db_session):
    _, keep_candidate = _fully_populated_candidate(db_session)
    _fully_populated_candidate(db_session)

    try:
        execute_selective_reset(db_session, retain_candidate_id=keep_candidate.id, confirm=False)
        assert False, "should have raised"
    except ValueError as exc:
        assert "confirm=True" in str(exc)

    # Nothing touched -- both candidates still present.
    assert dry_run_selective_counts(db_session, keep_candidate.id).total_to_delete == 7


def test_execute_selective_reset_refuses_for_nonexistent_candidate(db_session):
    _fully_populated_candidate(db_session)
    bogus_id = uuid.uuid4()

    try:
        execute_selective_reset(db_session, retain_candidate_id=bogus_id, confirm=True)
        assert False, "should have raised"
    except ValueError as exc:
        assert str(bogus_id) in str(exc)

    # Refusing to proceed means nothing was deleted -- the one real
    # candidate from the fixture is still there.
    assert db_session.execute(select(DiscoveryCandidate)).scalars().all() != []


def test_execute_selective_reset_keeps_only_the_named_candidate(db_session):
    """The central behavior: after running, the retained candidate and
    ONLY its own dependent rows remain; the other candidate and every one
    of its dependent rows (across every table this operation touches) are
    gone."""
    _, keep_candidate = _fully_populated_candidate(db_session)
    _, other_candidate = _fully_populated_candidate(db_session)

    result = execute_selective_reset(db_session, retain_candidate_id=keep_candidate.id, confirm=True)

    assert result.verified_clean is True
    assert result.pre_counts.total_to_delete == 7
    assert result.post_counts.total_to_delete == 0

    remaining_candidates = db_session.execute(select(DiscoveryCandidate)).scalars().all()
    assert [c.id for c in remaining_candidates] == [keep_candidate.id]

    for model, label in (
        (NewsletterItem, "newsletter_items"), (RagIngestionRequest, "rag_ingestion_requests"),
        (DiscoveryEditorialDraft, "editorial_drafts"), (DiscoveryAnalysis, "analysis"),
        (DiscoveryDocument, "documents"), (DiscoverySourceRecord, "source_records"),
    ):
        rows = db_session.execute(select(model)).scalars().all()
        assert len(rows) == 1, f"{label}: expected exactly 1 row to remain"
        assert rows[0].candidate_id == keep_candidate.id, f"{label}: the surviving row must be the retained candidate's"

    assert other_candidate.id not in [c.id for c in remaining_candidates]


def test_execute_selective_reset_removes_orphan_source_records(db_session):
    """A source_record with candidate_id=None (collected but never
    promoted to a candidate) is not "the retained candidate's" and must
    be cleaned up too."""
    source = DiscoverySource(
        source_name="Orphan Source", source_type="rss", source_tier="tier_2",
        collection_method="rss_atom", configuration={},
    )
    db_session.add(source)
    db_session.flush()
    orphan = DiscoverySourceRecord(
        external_id="orphan-ext-1", source_id=source.id, source_type="rss",
        title="Never became a candidate", first_seen_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc), content_hash="orphan-hash-1", candidate_id=None,
    )
    db_session.add(orphan)
    db_session.flush()

    _, keep_candidate = _fully_populated_candidate(db_session)

    execute_selective_reset(db_session, retain_candidate_id=keep_candidate.id, confirm=True)

    remaining_records = db_session.execute(select(DiscoverySourceRecord)).scalars().all()
    assert [r.candidate_id for r in remaining_records] == [keep_candidate.id]


def test_execute_selective_reset_preserves_sources_runs_taxonomy_jobs_and_audit(db_session):
    """Deliberate scope difference from the full execute_reset(): sources,
    runs, jobs, and taxonomy are untouched here even though execute_reset()
    clears runs -- this operation only cleans the CANDIDATE dataset, not
    collection/run history. Confirmed both structurally (the table list
    itself) and behaviorally (the actual rows survive, for BOTH the kept
    and the removed candidate's source/run)."""
    assert "discovery_sources" not in {name for name, _ in _SELECTIVE_TABLES_IN_DELETE_ORDER}
    assert "discovery_runs" not in {name for name, _ in _SELECTIVE_TABLES_IN_DELETE_ORDER}
    assert "discovery_jobs" not in {name for name, _ in _SELECTIVE_TABLES_IN_DELETE_ORDER}
    assert "discovery_taxonomy" not in {name for name, _ in _SELECTIVE_TABLES_IN_DELETE_ORDER}
    assert "discovery_audit_log" not in {name for name, _ in _SELECTIVE_TABLES_IN_DELETE_ORDER}

    seed_default_taxonomy(db_session)
    keep_source, keep_candidate = _fully_populated_candidate(db_session)
    other_source, other_candidate = _fully_populated_candidate(db_session)

    execute_selective_reset(db_session, retain_candidate_id=keep_candidate.id, confirm=True)

    # Both sources survive, including the REMOVED candidate's own source --
    # a source definition is configuration, not candidate data.
    assert db_session.get(DiscoverySource, keep_source.id) is not None
    assert db_session.get(DiscoverySource, other_source.id) is not None

    # Both runs survive for the same reason.
    runs = db_session.execute(select(DiscoveryRun)).scalars().all()
    assert len(runs) == 2

    # Taxonomy is config, untouched.
    assert len(db_session.execute(select(DiscoveryTaxonomy)).scalars().all()) > 0

    # Audit history survives for BOTH candidates, including the removed
    # one -- same "audit trail outlives the data it describes" principle
    # as execute_reset().
    audit_rows = db_session.execute(select(DiscoveryAuditLog)).scalars().all()
    assert {r.candidate_id for r in audit_rows} == {keep_candidate.id, other_candidate.id}
