"""
Tests for the frozen Candidate API contract (CMT Veda Final Functional
Requirements, spec item 3): the `page`/`page_size` pagination alias and
the `draft`/`drafted` newsletter-status terminology alias on
`GET /candidates`. The canonical contract itself (limit/offset,
`"drafted"`) is unchanged and already covered elsewhere
(tests/test_candidate_engine.py, tests/test_cmt_veda_compat.py) -- these
tests exist only to prove the two NEW input aliases resolve correctly
without changing what gets stored or what a `limit`/`offset` caller sees.
"""
import uuid as uuid_module

from app.models.candidate import DiscoveryCandidate
from app.services.workflows import newsletter_workflow as wf


def _create_candidate(app_client, reviewer_headers, url_suffix):
    resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": f"https://example.org/{url_suffix}",
            "title": f"Contract test article ({url_suffix})",
            "content_type": "research_paper",
            "source_name": "Manual Contract Test",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    return resp.json()["candidate_id"]


def test_page_page_size_alias_computes_the_same_canonical_envelope_as_limit_offset(app_client, reviewer_headers):
    ids = [_create_candidate(app_client, reviewer_headers, f"page-alias-{i}") for i in range(5)]

    direct = app_client.get("/api/v1/candidates", params={"limit": 2, "offset": 2}, headers=reviewer_headers)
    assert direct.status_code == 200

    via_page = app_client.get("/api/v1/candidates", params={"page": 2, "page_size": 2}, headers=reviewer_headers)
    assert via_page.status_code == 200
    body = via_page.json()

    # The response envelope is always the canonical limit/offset shape,
    # regardless of which input style the caller used.
    assert body["limit"] == 2
    assert body["offset"] == 2
    assert [item["id"] for item in body["items"]] == [item["id"] for item in direct.json()["items"]]


def test_explicit_limit_offset_is_unaffected_when_page_is_not_sent(app_client, reviewer_headers):
    for i in range(3):
        _create_candidate(app_client, reviewer_headers, f"limit-offset-unaffected-{i}")

    resp = app_client.get("/api/v1/candidates", params={"limit": 7, "offset": 1}, headers=reviewer_headers)
    assert resp.status_code == 200
    assert resp.json()["limit"] == 7
    assert resp.json()["offset"] == 1


def test_newsletter_status_draft_alias_matches_canonical_drafted_rows(app_client, db_session, reviewer_headers, admin_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "status-alias")
    candidate = db_session.get(DiscoveryCandidate, uuid_module.UUID(candidate_id))
    wf.select_for_newsletter(db_session, candidate, "reviewer:alias")
    wf.mark_drafted(db_session, candidate, uuid_module.uuid4())
    db_session.commit()
    assert candidate.newsletter_status == "drafted"  # canonical value, unchanged

    canonical = app_client.get(
        "/api/v1/candidates", params={"newsletter_status": "drafted"}, headers=reviewer_headers
    )
    via_alias = app_client.get(
        "/api/v1/candidates", params={"newsletter_status": "draft"}, headers=reviewer_headers
    )
    assert canonical.status_code == via_alias.status_code == 200
    assert candidate_id in [c["id"] for c in via_alias.json()["items"]]
    assert [c["id"] for c in via_alias.json()["items"]] == [c["id"] for c in canonical.json()["items"]]

    # The API never returns "draft" -- only the canonical "drafted".
    returned = next(c for c in via_alias.json()["items"] if c["id"] == candidate_id)
    assert returned["newsletter_status"] == "drafted"
