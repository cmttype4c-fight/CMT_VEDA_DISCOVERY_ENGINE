"""
Tests for `POST /candidates/{id}/newsletter/publish-now` (CMT Veda Final
Functional Requirements, spec item 8: "Explicit Publish Now from an
approved item should also be supported"). This is pure orchestration
over the already-tested `schedule`/`publish` transitions
(tests/test_newsletter_workflow.py) -- no new ALLOWED_TRANSITIONS edge --
so these tests focus on the orchestration itself: approved -> published
in one call, scheduled -> published in one call, and rejection from any
other state.
"""
import uuid as uuid_module

from app.models.candidate import DiscoveryCandidate
from app.services.workflows import newsletter_workflow as wf


def _create_candidate(app_client, reviewer_headers, url_suffix):
    resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": f"https://example.org/{url_suffix}",
            "title": f"Publish-now test article ({url_suffix})",
            "content_type": "research_paper",
            "source_name": "Manual Publish-Now Test",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    return resp.json()["candidate_id"]


def _advance_to_approved(db_session, candidate_id):
    candidate = db_session.get(DiscoveryCandidate, uuid_module.UUID(candidate_id))
    wf.select_for_newsletter(db_session, candidate, "reviewer:setup")
    wf.mark_drafted(db_session, candidate, uuid_module.uuid4())
    wf.submit_for_review(db_session, candidate, "reviewer:setup")
    wf.approve(db_session, candidate, "admin:setup")
    db_session.commit()
    return candidate


def test_publish_now_from_approved_reaches_published_in_one_call(app_client, db_session, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "approved-direct")
    _advance_to_approved(db_session, candidate_id)

    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/publish-now", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "published"
    assert body["published_at"] is not None

    candidate_resp = app_client.get(f"/api/v1/candidates/{candidate_id}", headers=admin_headers)
    assert candidate_resp.json()["newsletter_status"] == "published"


def test_publish_now_from_scheduled_publishes_immediately(app_client, db_session, admin_headers, reviewer_headers):
    from datetime import datetime, timedelta, timezone

    candidate_id = _create_candidate(app_client, reviewer_headers, "scheduled-then-now")
    _advance_to_approved(db_session, candidate_id)

    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    schedule_resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/newsletter/schedule", json={"scheduled_for": future}, headers=admin_headers,
    )
    assert schedule_resp.status_code == 200
    assert schedule_resp.json()["status"] == "scheduled"

    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/publish-now", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


def test_publish_now_from_an_unsupported_state_is_422_and_does_not_mutate(app_client, db_session, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "wrong-state")
    candidate = db_session.get(DiscoveryCandidate, uuid_module.UUID(candidate_id))
    wf.select_for_newsletter(db_session, candidate, "reviewer:setup")
    db_session.commit()  # left at 'selected', never approved/scheduled

    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/publish-now", headers=admin_headers)
    assert resp.status_code == 422

    candidate_resp = app_client.get(f"/api/v1/candidates/{candidate_id}", headers=admin_headers)
    assert candidate_resp.json()["newsletter_status"] == "selected"


def test_publish_now_requires_admin(app_client, db_session, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "needs-admin")
    _advance_to_approved(db_session, candidate_id)

    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/publish-now", headers=reviewer_headers)
    assert resp.status_code == 403
