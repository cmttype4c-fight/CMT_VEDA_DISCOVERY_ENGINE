import pytest

from app.models.newsletter import NewsletterPublication
from app.models.source import DiscoverySource
from app.services.candidate_service import create_candidate_from_source_record
from app.services.deduplication import deduplicate
from app.services.normalization import normalize_to_source_record
from app.services.workflows import newsletter_workflow as wf
from app.services.workflows.newsletter_workflow import InvalidTransition
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


def test_full_happy_path_to_published(db_session):
    import uuid
    from datetime import datetime, timezone

    candidate = _candidate(db_session)

    wf.select_for_newsletter(db_session, candidate, "reviewer:a")
    assert candidate.newsletter_status == "selected"

    wf.mark_drafted(db_session, candidate, uuid.uuid4())
    assert candidate.newsletter_status == "drafted"

    wf.submit_for_review(db_session, candidate, "reviewer:a")
    assert candidate.newsletter_status == "under_review"

    wf.approve(db_session, candidate, "admin:a")
    assert candidate.newsletter_status == "approved"

    publication = NewsletterPublication(title="Test Issue", status="scheduled")
    db_session.add(publication)
    db_session.flush()

    item = wf.schedule(db_session, candidate, "admin:a", datetime.now(timezone.utc), publication)
    assert candidate.newsletter_status == "scheduled"
    assert str(item.id) in publication.item_ids

    wf.publish(db_session, candidate, item, "admin:a")
    assert candidate.newsletter_status == "published"


def test_reject_then_resubmit_flow(db_session):
    candidate = _candidate(db_session)
    wf.select_for_newsletter(db_session, candidate, "reviewer:a")
    wf.mark_drafted(db_session, candidate, None)
    wf.submit_for_review(db_session, candidate, "reviewer:a")

    wf.reject(db_session, candidate, "admin:a", reason="Not clear enough on CMT relevance")
    assert candidate.newsletter_status == "rejected"

    item = wf.select_for_newsletter(db_session, candidate, "reviewer:a")
    assert candidate.newsletter_status == "selected"
    assert item.rejection_reason == "Not clear enough on CMT relevance"  # preserved for history


def test_cannot_skip_states(db_session):
    """State transitions are protected server-side (spec #50) -- a
    candidate cannot jump straight from not_selected to approved."""
    candidate = _candidate(db_session)
    with pytest.raises(InvalidTransition):
        wf.approve(db_session, candidate, "admin:a")


def test_cannot_publish_before_scheduled(db_session):
    candidate = _candidate(db_session)
    item = wf.get_or_create_item(db_session, candidate)
    with pytest.raises(InvalidTransition):
        wf.publish(db_session, candidate, item, "admin:a")


def test_archive_reachable_from_most_states(db_session):
    candidate = _candidate(db_session)
    wf.select_for_newsletter(db_session, candidate, "reviewer:a")
    wf.archive(db_session, candidate, "admin:a")
    assert candidate.newsletter_status == "archived"
