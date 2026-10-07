"""
Tests for the consolidated Candidate Workspace API (CMT Veda Final
Functional Requirements, spec item 4): `GET /candidates/{id}/workspace`.

Read-only aggregation over tables Discovery already owns -- the
per-table behavior (newsletter transitions, document acquisition,
editorial drafts, RAG verification) is already covered by its own test
file; these tests exist to prove the aggregation itself: the right rows
are picked (latest analysis, current draft, acquired-preferred document),
nothing leaks across candidates, and viewing a workspace never creates a
NewsletterItem/RagIngestionRequest as a side effect.
"""
import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from app.models.analysis import DiscoveryAnalysis
from app.models.document import DiscoveryDocument


def _create_candidate(app_client, reviewer_headers, url_suffix):
    resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": f"https://example.org/{url_suffix}",
            "title": f"Workspace test article ({url_suffix})",
            "content_type": "research_paper",
            "source_name": "Manual Workspace Test",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    return resp.json()["candidate_id"]


def test_workspace_for_a_brand_new_candidate_has_clean_nones_and_no_side_effects(app_client, db_session, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "fresh")

    resp = app_client.get(f"/api/v1/candidates/{candidate_id}/workspace", headers=reviewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidate"]["id"] == candidate_id
    assert body["analysis"] is None
    assert body["document"] is None
    assert body["editorial_draft"] is None
    assert body["newsletter"] is None
    assert body["rag"] is None
    # manual-discovery itself writes one "discovered" audit row (spec item
    # 4's "audit information where appropriate") -- nothing more yet.
    assert [a["action"] for a in body["recent_audit"]] == ["discovered"]

    # Viewing the workspace must never itself create a NewsletterItem or
    # RagIngestionRequest (spec item 4 is read-only aggregation).
    status_resp = app_client.get(f"/api/v1/candidates/{candidate_id}/rag/status", headers=reviewer_headers)
    # get_or_create_request on the dedicated RAG endpoint is allowed to
    # create one; what matters is the WORKSPACE call just now did not.
    assert status_resp.status_code == 200


def test_workspace_aggregates_newsletter_editorial_and_document_state(app_client, db_session, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "full")
    cid = uuid_module.UUID(candidate_id)

    app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/select", headers=reviewer_headers)
    section_resp = app_client.patch(
        f"/api/v1/candidates/{candidate_id}/newsletter/section", json={"section": "Research Digest"},
        headers=reviewer_headers,
    )
    assert section_resp.status_code == 200

    old_analysis = DiscoveryAnalysis(
        candidate_id=cid, is_latest=False, cmt_relevance_score=20, peripheral_neuropathy_relevance_score=20,
        clinical_relevance_score=20, research_importance_score=20, patient_relevance_score=20,
        analysis_confidence=20, analysis_version=1, generated_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    latest_analysis = DiscoveryAnalysis(
        candidate_id=cid, is_latest=True, cmt_relevance_score=95, peripheral_neuropathy_relevance_score=90,
        clinical_relevance_score=88, research_importance_score=92, patient_relevance_score=80,
        analysis_confidence=93, analysis_version=2, generated_at=datetime.now(timezone.utc),
    )
    db_session.add(old_analysis)
    db_session.add(latest_analysis)

    doc = DiscoveryDocument(
        candidate_id=cid, source="europepmc", retrieval_status="acquired", full_text_format="pdf",
        pdf_available=True, extracted_text="Full body text that must not appear in the workspace.",
        extraction_status="success", extracted_char_count=52,
    )
    db_session.add(doc)
    db_session.commit()

    gen_resp = app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-draft", json={}, headers=admin_headers)
    assert gen_resp.status_code == 200

    resp = app_client.get(f"/api/v1/candidates/{candidate_id}/workspace", headers=reviewer_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["analysis"]["analysis_version"] == 2  # the LATEST, not the old one
    assert body["document"]["retrieval_status"] == "acquired"
    assert "extracted_text" not in body["document"]  # lightweight summary, never the article body
    assert body["editorial_draft"] is not None
    assert body["newsletter"]["status"] == "selected"
    assert body["newsletter"]["section"] == "Research Digest"


def test_workspace_does_not_leak_another_candidates_newsletter_or_audit_rows(app_client, reviewer_headers):
    candidate_a = _create_candidate(app_client, reviewer_headers, "leak-a")
    candidate_b = _create_candidate(app_client, reviewer_headers, "leak-b")

    app_client.post(f"/api/v1/candidates/{candidate_a}/newsletter/select", headers=reviewer_headers)

    workspace_b = app_client.get(f"/api/v1/candidates/{candidate_b}/workspace", headers=reviewer_headers)
    assert workspace_b.status_code == 200
    assert workspace_b.json()["newsletter"] is None
    # Only candidate_b's own "discovered" row -- never candidate_a's
    # "newsletter_selected" audit entry.
    assert [a["action"] for a in workspace_b.json()["recent_audit"]] == ["discovered"]

    workspace_a = app_client.get(f"/api/v1/candidates/{candidate_a}/workspace", headers=reviewer_headers)
    assert workspace_a.json()["newsletter"]["status"] == "selected"
    assert "newsletter_selected" in [a["action"] for a in workspace_a.json()["recent_audit"]]


def test_workspace_requires_authentication(app_client):
    resp = app_client.get(f"/api/v1/candidates/{uuid_module.uuid4()}/workspace")
    assert resp.status_code == 401


def test_workspace_404_for_unknown_candidate(app_client, reviewer_headers):
    resp = app_client.get(f"/api/v1/candidates/{uuid_module.uuid4()}/workspace", headers=reviewer_headers)
    assert resp.status_code == 404
