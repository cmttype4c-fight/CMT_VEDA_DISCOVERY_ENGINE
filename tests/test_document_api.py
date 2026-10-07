"""
Tests for the original document/full-text API (CMT Veda Final Functional
Requirements, spec items 2, 5, 6, 7): `GET /candidates/{id}/document` and
`GET /documents/{id}`.

There is no HTTP endpoint that CREATES a `discovery_documents` row --
that only ever happens through `app/services/fulltext/service.py::
acquire_full_text`, called by the worker (app/worker/handlers.py::
handle_resolve_full_text), which is already covered by
tests/test_fulltext.py. These tests insert `DiscoveryDocument` rows
directly via `db_session` (the same established pattern
tests/test_cmt_veda_compat.py's `_advance_to_approved` helper uses for
other steps with no dedicated HTTP endpoint) and verify the READ side.
"""
import uuid as uuid_module
from datetime import datetime, timedelta, timezone

from app.models.document import DiscoveryDocument


def _create_candidate(app_client, reviewer_headers, url_suffix):
    resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": f"https://example.org/{url_suffix}",
            "title": f"Document API test article ({url_suffix})",
            "content_type": "research_paper",
            "source_name": "Manual Document Test",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    return resp.json()["candidate_id"]


def test_document_404_when_full_text_resolution_was_never_attempted(app_client, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "never-attempted")
    resp = app_client.get(f"/api/v1/candidates/{candidate_id}/document", headers=reviewer_headers)
    assert resp.status_code == 404


def test_document_200_with_extracted_text_when_acquired(app_client, db_session, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "acquired")
    doc = DiscoveryDocument(
        candidate_id=uuid_module.UUID(candidate_id), source="europepmc", full_text_source="europepmc_oa_xml",
        document_url="https://europepmc.org/article/x", retrieved_at=datetime.now(timezone.utc),
        mime_type="application/xml", full_text_format="xml", pdf_available=False, file_size=4096,
        content_hash="abc123", document_ref="/data/discovery-documents/abc123.xml",
        license_provenance="Europe PMC open-access full text", retrieval_status="acquired",
        extracted_text="The acquired article body, extracted verbatim.", extracted_char_count=46,
        extraction_status="success",
    )
    db_session.add(doc)
    db_session.commit()

    resp = app_client.get(f"/api/v1/candidates/{candidate_id}/document", headers=reviewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["retrieval_status"] == "acquired"
    assert body["full_text_format"] == "xml"
    assert body["extracted_text"] == "The acquired article body, extracted verbatim."
    assert body["candidate_id"] == candidate_id

    by_id = app_client.get(f"/api/v1/documents/{body['id']}", headers=reviewer_headers)
    assert by_id.status_code == 200
    assert by_id.json()["id"] == body["id"]


def test_document_200_but_no_extracted_text_when_unavailable(app_client, db_session, reviewer_headers):
    """Spec item 2: 'abstract-only material must not be represented as
    full text.' An engine that genuinely tried and found no open-access
    copy still returns 200 (it DID attempt resolution) but `extracted_text`
    must stay null -- never fall back to the abstract here."""
    candidate_id = _create_candidate(app_client, reviewer_headers, "unavailable")
    doc = DiscoveryDocument(
        candidate_id=uuid_module.UUID(candidate_id), source="europepmc", retrieval_status="unavailable",
        error_detail="no open-access copy found", extraction_status="not_attempted",
    )
    db_session.add(doc)
    db_session.commit()

    resp = app_client.get(f"/api/v1/candidates/{candidate_id}/document", headers=reviewer_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["retrieval_status"] == "unavailable"
    assert body["extracted_text"] is None
    assert body["error_detail"] == "no open-access copy found"


def test_document_prefers_the_acquired_row_over_a_later_retry_attempt(app_client, db_session, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "mixed-history")
    cid = uuid_module.UUID(candidate_id)
    acquired = DiscoveryDocument(
        candidate_id=cid, source="europepmc", retrieval_status="acquired", full_text_format="pdf",
        pdf_available=True, extracted_text="Original acquired text.", extraction_status="success",
    )
    db_session.add(acquired)
    db_session.commit()

    later_retry_failed = DiscoveryDocument(
        candidate_id=cid, source="europepmc", retrieval_status="failed",
        error_detail="retry attempt failed", extraction_status="not_attempted",
    )
    db_session.add(later_retry_failed)
    db_session.commit()

    resp = app_client.get(f"/api/v1/candidates/{candidate_id}/document", headers=reviewer_headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == str(acquired.id)
    assert resp.json()["extracted_text"] == "Original acquired text."


def test_document_endpoint_requires_authentication(app_client):
    resp = app_client.get(f"/api/v1/candidates/{uuid_module.uuid4()}/document")
    assert resp.status_code == 401
