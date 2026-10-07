"""
Tests for `PATCH /candidates/{id}/newsletter/section` (CMT Veda Final
Functional Requirements, spec item 9: "persistent Newsletter
distribution ... the selected Newsletter section/destination must not
exist only in browser state").
"""
import uuid as uuid_module

from sqlalchemy import select

from app.models.audit import DiscoveryAuditLog


def _create_candidate(app_client, reviewer_headers, url_suffix):
    resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": f"https://example.org/{url_suffix}",
            "title": f"Section test article ({url_suffix})",
            "content_type": "research_paper",
            "source_name": "Manual Section Test",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    return resp.json()["candidate_id"]


def test_section_persists_and_survives_a_fresh_read(app_client, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "persist")
    app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/select", headers=reviewer_headers)

    resp = app_client.patch(
        f"/api/v1/candidates/{candidate_id}/newsletter/section", json={"section": "Research Digest"},
        headers=reviewer_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["section"] == "Research Digest"
    assert resp.json()["status"] == "selected"  # pure metadata, no state-machine transition

    # A SEPARATE request (simulating a different device/reviewer re-opening
    # the page) sees the persisted value -- it is not browser-local state.
    workspace_resp = app_client.get(f"/api/v1/candidates/{candidate_id}/workspace", headers=reviewer_headers)
    assert workspace_resp.json()["newsletter"]["section"] == "Research Digest"


def test_section_can_be_set_before_the_item_is_even_selected(app_client, reviewer_headers):
    """A pure metadata assignment, deliberately not gated by status."""
    candidate_id = _create_candidate(app_client, reviewer_headers, "pre-stage")
    resp = app_client.patch(
        f"/api/v1/candidates/{candidate_id}/newsletter/section", json={"section": "Community Spotlight"},
        headers=reviewer_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_selected"
    assert resp.json()["section"] == "Community Spotlight"


def test_section_update_writes_an_audit_entry(app_client, db_session, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "audited")
    app_client.patch(
        f"/api/v1/candidates/{candidate_id}/newsletter/section", json={"section": "Gene Therapy Watch"},
        headers=reviewer_headers,
    )
    rows = db_session.execute(
        select(DiscoveryAuditLog).where(
            DiscoveryAuditLog.candidate_id == uuid_module.UUID(candidate_id),
            DiscoveryAuditLog.action == "section_assigned",
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].new_value == {"section": "Gene Therapy Watch"}


def test_section_rejects_empty_string(app_client, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "empty")
    resp = app_client.patch(
        f"/api/v1/candidates/{candidate_id}/newsletter/section", json={"section": ""}, headers=reviewer_headers
    )
    assert resp.status_code == 422


def test_section_requires_authentication(app_client):
    resp = app_client.patch(
        f"/api/v1/candidates/{uuid_module.uuid4()}/newsletter/section", json={"section": "X"}
    )
    assert resp.status_code == 401
