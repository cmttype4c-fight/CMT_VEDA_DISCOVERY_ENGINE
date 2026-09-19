"""
End-to-end API test approximating the final acceptance test (spec #59):
manual discovery -> candidate -> analysis -> editorial draft -> newsletter
review chain, and independently candidate -> RAG review -> verification ->
approval -> (mock) ingestion adapter -> indexed status. Runs entirely
against the in-memory SQLite DB and mock AI/RAG providers -- no network.
"""


def test_source_crud_and_run_trigger(app_client, admin_headers):
    create_resp = app_client.post(
        "/api/v1/sources",
        json={
            "source_name": "PubMed E2E Test",
            "source_type": "pubmed",
            "source_tier": "tier_1",
            "collection_method": "official_api",
            "configuration": {"collector": "pubmed"},
        },
        headers=admin_headers,
    )
    assert create_resp.status_code == 201
    source_id = create_resp.json()["id"]

    get_resp = app_client.get(f"/api/v1/sources/{source_id}", headers=admin_headers)
    assert get_resp.status_code == 200

    # "Run now" is idempotent (spec #34): a second immediate call returns
    # the same in-flight job rather than creating a duplicate.
    run_resp_1 = app_client.post(f"/api/v1/sources/{source_id}/run", headers=admin_headers)
    run_resp_2 = app_client.post(f"/api/v1/sources/{source_id}/run", headers=admin_headers)
    assert run_resp_1.status_code == 200
    assert run_resp_2.status_code == 200
    assert run_resp_1.json()["job_id"] == run_resp_2.json()["job_id"]


def test_manual_discovery_through_editorial_and_newsletter_review(app_client, admin_headers, reviewer_headers):
    manual_resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": "https://example.org/e2e-article",
            "title": "End-to-end test article about Charcot-Marie-Tooth disease",
            "content_type": "research_paper",
            "source_name": "Manual E2E",
        },
        headers=reviewer_headers,
    )
    assert manual_resp.status_code == 201
    candidate_id = manual_resp.json()["candidate_id"]
    assert candidate_id is not None

    analyse_resp = app_client.post(f"/api/v1/candidates/{candidate_id}/analyse", json={}, headers=admin_headers)
    assert analyse_resp.status_code == 200
    assert 0 <= analyse_resp.json()["cmt_relevance_score"] <= 100

    draft_resp = app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-draft", json={}, headers=admin_headers)
    assert draft_resp.status_code == 200
    assert draft_resp.json()["disclaimer"]

    select_resp = app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/select", headers=reviewer_headers)
    assert select_resp.status_code == 200
    assert select_resp.json()["status"] == "selected"

    candidate_resp = app_client.get(f"/api/v1/candidates/{candidate_id}", headers=admin_headers)
    assert candidate_resp.json()["newsletter_status"] == "selected"


def test_manual_discovery_through_rag_approval_and_mock_ingestion(app_client, admin_headers, reviewer_headers):
    manual_resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": "https://example.org/e2e-rag-article",
            "title": "A CMT1A PMP22 gene therapy trial summary",
            "content_type": "clinical_guidance",
            "source_name": "Manual E2E",
        },
        headers=reviewer_headers,
    )
    candidate_id = manual_resp.json()["candidate_id"]

    submit_resp = app_client.post(f"/api/v1/candidates/{candidate_id}/rag/submit", json={}, headers=reviewer_headers)
    assert submit_resp.json()["status"] == "pending_approval"

    verify_resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/rag/verify",
        json={
            "source_verified": True,
            "original_source_accessible": True,
            "scientific_relevance_confirmed": True,
            "suitable_for_ask_veda": True,
            "content_permitted_for_ingestion": True,
        },
        headers=reviewer_headers,
    )
    assert verify_resp.status_code == 200

    approve_resp = app_client.post(f"/api/v1/candidates/{candidate_id}/rag/approve", headers=admin_headers)
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "indexed"  # mock adapter "indexes" synchronously
    assert approve_resp.json()["external_ingestion_id"].startswith("mock-ingest-")


def test_rag_verify_rejected_when_not_pending_approval(app_client, admin_headers, reviewer_headers):
    """API-level check that the state restriction on verify() (targeted
    fix) surfaces as 409 Conflict, not a 500, and not a silent success."""
    manual_resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": "https://example.org/verify-state-check",
            "title": "Verify state-restriction test article",
            "content_type": "research_paper",
            "source_name": "Manual E2E",
        },
        headers=reviewer_headers,
    )
    candidate_id = manual_resp.json()["candidate_id"]

    verify_payload = {
        "source_verified": True,
        "original_source_accessible": True,
        "scientific_relevance_confirmed": True,
        "suitable_for_ask_veda": True,
        "content_permitted_for_ingestion": True,
    }

    # Never submitted -- still not_selected.
    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/rag/verify", json=verify_payload, headers=reviewer_headers
    )
    assert resp.status_code == 409

    # Submit, verify, approve -- the mock adapter carries this all the way
    # to "indexed" synchronously (see the approve() router). Verifying
    # again afterwards must also be rejected, not silently accepted,
    # regardless of which non-pending_approval state it landed in.
    app_client.post(f"/api/v1/candidates/{candidate_id}/rag/submit", json={}, headers=reviewer_headers)
    ok_resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/rag/verify", json=verify_payload, headers=reviewer_headers
    )
    assert ok_resp.status_code == 200
    app_client.post(f"/api/v1/candidates/{candidate_id}/rag/approve", headers=admin_headers)

    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/rag/verify", json=verify_payload, headers=reviewer_headers
    )
    assert resp.status_code == 409


def test_candidate_filtering_and_pagination(app_client, admin_headers, reviewer_headers):
    for i in range(3):
        app_client.post(
            "/api/v1/manual-discovery",
            json={
                "url": f"https://example.org/pagination-test-{i}",
                "title": f"Pagination test article {i}",
                "content_type": "research_news",
                "source_name": "Manual E2E",
            },
            headers=reviewer_headers,
        )

    resp = app_client.get("/api/v1/candidates?limit=2&offset=0", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] >= 3
    assert body["has_more"] is True

    q_resp = app_client.get("/api/v1/candidates?q=Pagination test article 1", headers=admin_headers)
    assert q_resp.status_code == 200
    assert any("Pagination test article 1" in item["title"] for item in q_resp.json()["items"])


def test_candidate_manual_override_via_api(app_client, admin_headers, reviewer_headers):
    manual_resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": "https://example.org/override-test",
            "title": "Override test article",
            "content_type": "research_paper",
            "source_name": "Manual E2E",
        },
        headers=reviewer_headers,
    )
    candidate_id = manual_resp.json()["candidate_id"]

    # Reviewer cannot override -- admin only (spec #29 + #40).
    resp = app_client.patch(
        f"/api/v1/candidates/{candidate_id}", json={"scope": "cmt_specific"}, headers=reviewer_headers
    )
    assert resp.status_code == 403

    resp = app_client.patch(
        f"/api/v1/candidates/{candidate_id}",
        json={"scope": "cmt_specific", "reason": "Confirmed via manual review"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["scope"] == "cmt_specific"
    assert resp.json()["has_manual_override"] is True
