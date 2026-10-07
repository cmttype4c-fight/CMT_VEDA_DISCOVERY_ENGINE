"""
Tests for `GET /newsletter/published` (CMT Veda Final Functional
Requirements, spec item 10): section filter, limit/offset, newest-first
ordering, and that the response carries enough to render the public
Newsletter without a follow-up per-candidate request.
"""
import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from app.models.candidate import DiscoveryCandidate
from app.services.workflows import newsletter_workflow as wf


def _create_candidate(app_client, reviewer_headers, url_suffix):
    resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": f"https://example.org/{url_suffix}",
            "title": f"Published feed test article ({url_suffix})",
            "content_type": "research_paper",
            "source_name": "Manual Feed Test",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    return resp.json()["candidate_id"]


def _publish(app_client, db_session, admin_headers, reviewer_headers, candidate_id, section=None):
    app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/select", headers=reviewer_headers)
    # Generates a REAL, current DiscoveryEditorialDraft row (mock AI
    # provider) -- needed so the feed's "headline"/"is_ai_generated"
    # fields have something real to attach, same pattern
    # tests/test_cmt_veda_compat.py::_advance_to_approved uses.
    gen_resp = app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-draft", json={}, headers=admin_headers)
    assert gen_resp.status_code == 200

    candidate = db_session.get(DiscoveryCandidate, uuid_module.UUID(candidate_id))
    wf.mark_drafted(db_session, candidate, uuid_module.uuid4())
    wf.submit_for_review(db_session, candidate, "reviewer:setup")
    wf.approve(db_session, candidate, "admin:setup")
    db_session.commit()
    if section:
        app_client.patch(
            f"/api/v1/candidates/{candidate_id}/newsletter/section", json={"section": section}, headers=reviewer_headers,
        )
    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/publish-now", headers=admin_headers)
    assert resp.status_code == 200


def test_published_feed_excludes_unpublished_items(app_client, db_session, admin_headers, reviewer_headers):
    published_id = _create_candidate(app_client, reviewer_headers, "feed-published")
    _publish(app_client, db_session, admin_headers, reviewer_headers, published_id)

    unpublished_id = _create_candidate(app_client, reviewer_headers, "feed-unpublished")
    app_client.post(f"/api/v1/candidates/{unpublished_id}/newsletter/select", headers=reviewer_headers)

    resp = app_client.get("/api/v1/newsletter/published", headers=reviewer_headers)
    assert resp.status_code == 200
    ids = [item["candidate_id"] for item in resp.json()["items"]]
    assert published_id in ids
    assert unpublished_id not in ids


def test_published_feed_is_newest_first_and_filters_by_section(app_client, db_session, admin_headers, reviewer_headers):
    older_id = _create_candidate(app_client, reviewer_headers, "feed-older")
    _publish(app_client, db_session, admin_headers, reviewer_headers, older_id, section="Research Digest")

    newer_id = _create_candidate(app_client, reviewer_headers, "feed-newer")
    _publish(app_client, db_session, admin_headers, reviewer_headers, newer_id, section="Community Spotlight")

    resp = app_client.get("/api/v1/newsletter/published", headers=reviewer_headers)
    ids_in_order = [item["candidate_id"] for item in resp.json()["items"]]
    assert ids_in_order.index(newer_id) < ids_in_order.index(older_id)

    filtered = app_client.get(
        "/api/v1/newsletter/published", params={"section": "Research Digest"}, headers=reviewer_headers
    )
    filtered_ids = [item["candidate_id"] for item in filtered.json()["items"]]
    assert older_id in filtered_ids
    assert newer_id not in filtered_ids


def test_published_feed_item_carries_enough_to_render_without_a_follow_up_call(app_client, db_session, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "feed-render")
    _publish(app_client, db_session, admin_headers, reviewer_headers, candidate_id)

    resp = app_client.get("/api/v1/newsletter/published", headers=reviewer_headers)
    item = next(i for i in resp.json()["items"] if i["candidate_id"] == candidate_id)

    # Scientific-source fields (candidate's own).
    assert item["title"]
    assert "content_type" in item
    # Editorial/AI content (current draft), clearly distinguishable.
    assert item["headline"]
    assert item["is_ai_generated"] is True


def test_published_feed_respects_limit_and_offset(app_client, db_session, admin_headers, reviewer_headers):
    ids = []
    for i in range(3):
        cid = _create_candidate(app_client, reviewer_headers, f"feed-page-{i}")
        _publish(app_client, db_session, admin_headers, reviewer_headers, cid)
        ids.append(cid)

    page = app_client.get("/api/v1/newsletter/published", params={"limit": 1, "offset": 1}, headers=reviewer_headers)
    assert page.status_code == 200
    assert len(page.json()["items"]) == 1
    assert page.json()["limit"] == 1
    assert page.json()["offset"] == 1
    assert page.json()["total"] >= 3


def test_published_feed_requires_authentication(app_client):
    resp = app_client.get("/api/v1/newsletter/published")
    assert resp.status_code == 401
