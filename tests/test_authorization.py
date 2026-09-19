"""
Authorization tests (spec #40): administrative operations must be
enforced server-side regardless of what the frontend does or doesn't show.
"""


def test_missing_token_is_rejected(app_client):
    resp = app_client.get("/api/v1/sources")
    assert resp.status_code == 401


def test_invalid_token_is_rejected(app_client):
    resp = app_client.get("/api/v1/sources", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_reviewer_cannot_create_source(app_client, reviewer_headers):
    payload = {
        "source_name": "Should Fail",
        "source_type": "rss",
        "source_tier": "tier_2",
        "collection_method": "rss_atom",
    }
    resp = app_client.post("/api/v1/sources", json=payload, headers=reviewer_headers)
    assert resp.status_code == 403


def test_admin_can_create_source(app_client, admin_headers):
    payload = {
        "source_name": "Admin Created Source",
        "source_type": "rss",
        "source_tier": "tier_2",
        "collection_method": "rss_atom",
    }
    resp = app_client.post("/api/v1/sources", json=payload, headers=admin_headers)
    assert resp.status_code == 201
    assert resp.json()["source_name"] == "Admin Created Source"


def test_reviewer_can_list_sources(app_client, reviewer_headers):
    resp = app_client.get("/api/v1/sources", headers=reviewer_headers)
    assert resp.status_code == 200


def test_reviewer_cannot_approve_rag_ingestion(app_client, admin_headers, reviewer_headers):
    # Create a source and manually-submit a candidate as a reviewer.
    manual_payload = {
        "url": "https://example.org/authz-test-article",
        "title": "Authorization test article",
        "content_type": "research_paper",
        "source_name": "Manual",
    }
    resp = app_client.post("/api/v1/manual-discovery", json=manual_payload, headers=reviewer_headers)
    assert resp.status_code == 201
    candidate_id = resp.json()["candidate_id"]

    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/rag/submit", json={}, headers=reviewer_headers)
    assert resp.status_code == 200

    # Reviewer cannot approve -- admin-only.
    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/rag/approve", headers=reviewer_headers)
    assert resp.status_code == 403


def test_health_endpoint_requires_no_auth(app_client):
    resp = app_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
