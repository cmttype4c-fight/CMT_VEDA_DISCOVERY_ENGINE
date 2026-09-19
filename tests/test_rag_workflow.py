import pytest

from app.models.source import DiscoverySource
from app.services.candidate_service import create_candidate_from_source_record
from app.services.deduplication import deduplicate
from app.services.normalization import normalize_to_source_record
from app.services.rag_adapter import MockRAGIngestionAdapter
from app.services.workflows import rag_workflow as wf
from app.services.workflows.rag_workflow import InvalidTransition, VerificationIncomplete
from tests.fixtures.golden_dataset import CMT_SPECIFIC_RESEARCH


def _candidate(db):
    source = DiscoverySource(
        source_name="Test PubMed", source_type="pubmed", source_tier="tier_1",
        collection_method="official_api", configuration={"collector": "pubmed"},
    )
    db.add(source)
    db.flush()
    record = normalize_to_source_record(CMT_SPECIFIC_RESEARCH, source_id=source.id, source_type=source.source_type)
    result = deduplicate(db, record)
    return create_candidate_from_source_record(db, result.record, source)


def _verify_all_true(db, candidate, performed_by="reviewer:a"):
    return wf.verify(
        db, candidate, performed_by,
        source_verified=True, original_source_accessible=True, scientific_relevance_confirmed=True,
        suitable_for_ask_veda=True, content_permitted_for_ingestion=True,
    )


def test_approve_requires_all_checks_to_pass(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")

    # Only some checks pass.
    wf.verify(
        db_session, candidate, "reviewer:a",
        source_verified=True, original_source_accessible=True, scientific_relevance_confirmed=False,
        suitable_for_ask_veda=True, content_permitted_for_ingestion=True,
    )
    with pytest.raises(VerificationIncomplete):
        wf.approve(db_session, candidate, "admin:a")


def test_approve_succeeds_once_all_checks_pass(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    req = wf.approve(db_session, candidate, "admin:a")
    assert req.status == "approved"
    assert candidate.rag_status == "approved"


def test_cannot_approve_without_submission(db_session):
    candidate = _candidate(db_session)
    with pytest.raises(InvalidTransition):
        wf.approve(db_session, candidate, "admin:a")


def test_technical_failure_produces_failed_not_rejected(db_session):
    """spec #51: processing -> failed for technical failure, NEVER rejected."""
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    req = wf.approve(db_session, candidate, "admin:a")
    req = wf.enqueue(db_session, candidate, req, {"title": candidate.title}, "v1")
    req = wf.mark_processing(db_session, candidate, req)

    req = wf.mark_failed(db_session, candidate, req, "adapter timeout")
    assert req.status == "failed"
    assert candidate.rag_status == "failed"
    assert req.status != "rejected"


def test_human_rejection_is_a_distinct_status_from_failed(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    req = wf.reject(db_session, candidate, "admin:a", reason="Source not sufficiently authoritative")
    assert req.status == "rejected"
    assert req.rejection_reason == "Source not sufficiently authoritative"


def test_retry_after_failure_requeues(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    req = wf.approve(db_session, candidate, "admin:a")
    req = wf.enqueue(db_session, candidate, req, {}, "v1")
    req = wf.mark_processing(db_session, candidate, req)
    req = wf.mark_failed(db_session, candidate, req, "timeout")

    req = wf.retry(db_session, candidate, req, "admin:a")
    assert req.status == "queued"


async def test_mock_adapter_full_round_trip(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    req = wf.approve(db_session, candidate, "admin:a")
    req = wf.enqueue(db_session, candidate, req, {"title": candidate.title}, "v1")
    req = wf.mark_processing(db_session, candidate, req)

    adapter = MockRAGIngestionAdapter()
    result = await adapter.submit({"title": candidate.title})
    assert result.success is True

    req = wf.mark_indexed(db_session, candidate, req, result.external_ingestion_id)
    assert req.status == "indexed"
    assert candidate.rag_status == "indexed"
    assert req.external_ingestion_id == result.external_ingestion_id


def test_remove_preserves_history(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    req = wf.approve(db_session, candidate, "admin:a")
    req = wf.enqueue(db_session, candidate, req, {}, "v1")
    req = wf.mark_processing(db_session, candidate, req)
    req = wf.mark_indexed(db_session, candidate, req, "ext-123")

    req = wf.remove(db_session, candidate, req, "admin:a")
    assert req.status == "removed"
    assert req.removed_at is not None
    # Row still exists (archived, not deleted) -- spec #52.
    from app.models.rag import RagIngestionRequest

    still_there = db_session.get(RagIngestionRequest, req.id)
    assert still_there is not None


# --- verify() is restricted to pending_approval (added per targeted fix) ---


def test_verify_rejected_when_not_selected(db_session):
    """A request that was never submitted is 'not_selected' -- verifying
    it makes no sense (there's nothing pending approval yet)."""
    candidate = _candidate(db_session)
    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)


def test_verify_rejected_when_rejected(db_session):
    """A rejected request must be resubmitted (back to pending_approval)
    before it can be verified again -- verifying it while still
    'rejected' must not silently succeed."""
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    wf.reject(db_session, candidate, "admin:a", reason="not ready")

    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)

    # Resubmitting puts it back into pending_approval, where verify() is
    # valid again.
    wf.submit(db_session, candidate, "reviewer:a")
    req = _verify_all_true(db_session, candidate)
    assert req.source_verified is True


def test_verify_rejected_when_approved(db_session):
    """Once approved, the checklist must not be silently editable --
    doing so would make the audit trail stop describing the state that
    was actually approved."""
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    wf.approve(db_session, candidate, "admin:a")

    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)


def test_verify_rejected_when_queued_processing_or_indexed(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    req = wf.approve(db_session, candidate, "admin:a")

    req = wf.enqueue(db_session, candidate, req, {}, "v1")
    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)

    req = wf.mark_processing(db_session, candidate, req)
    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)

    wf.mark_indexed(db_session, candidate, req, "ext-999")
    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)


def test_verify_rejected_when_failed(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    req = wf.approve(db_session, candidate, "admin:a")
    req = wf.enqueue(db_session, candidate, req, {}, "v1")
    req = wf.mark_processing(db_session, candidate, req)
    wf.mark_failed(db_session, candidate, req, "adapter timeout")

    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)


def test_verify_rejected_when_removed(db_session):
    candidate = _candidate(db_session)
    wf.submit(db_session, candidate, "reviewer:a")
    _verify_all_true(db_session, candidate)
    req = wf.approve(db_session, candidate, "admin:a")
    req = wf.enqueue(db_session, candidate, req, {}, "v1")
    req = wf.mark_processing(db_session, candidate, req)
    req = wf.mark_indexed(db_session, candidate, req, "ext-1")
    wf.remove(db_session, candidate, req, "admin:a")

    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)


def test_verify_does_not_mutate_flags_when_rejected_by_state_check(db_session):
    """Belt-and-suspenders: confirm the checklist flags on the request
    are genuinely untouched when verify() is correctly rejected, not
    just that an exception was raised."""
    candidate = _candidate(db_session)
    req = wf.get_or_create_request(db_session, candidate)
    assert req.source_verified is False

    with pytest.raises(InvalidTransition):
        _verify_all_true(db_session, candidate)

    db_session.refresh(req)
    assert req.source_verified is False
    assert req.verified_at is None
