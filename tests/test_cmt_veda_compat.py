"""
Tests for the CMT Veda compatibility adapters added to the existing
Discovery Engine API. Every route under test here is a thin wrapper over
existing workflow functions (see app/api/routers/newsletter.py,
editorial.py, runs.py, and the new overview.py) -- these tests exist to
confirm the wrapping/dispatch/auth logic is correct, not to re-test the
underlying newsletter_workflow.py transitions themselves (already
covered by tests/test_newsletter_workflow.py).
"""
from datetime import datetime, timedelta, timezone


def _create_candidate(app_client, reviewer_headers, url_suffix="compat-test"):
    resp = app_client.post(
        "/api/v1/manual-discovery",
        json={
            "url": f"https://example.org/{url_suffix}",
            "title": f"CMT Veda compat test article ({url_suffix})",
            "content_type": "research_paper",
            "source_name": "Manual Compat Test",
        },
        headers=reviewer_headers,
    )
    assert resp.status_code == 201
    return resp.json()["candidate_id"]


# --- /overview ---


def test_overview_returns_aggregate_counts(app_client, admin_headers, reviewer_headers):
    app_client.post(
        "/api/v1/sources",
        json={
            "source_name": "Overview Test Source",
            "source_type": "rss",
            "source_tier": "tier_2",
            "collection_method": "rss_atom",
        },
        headers=admin_headers,
    )
    _create_candidate(app_client, reviewer_headers, "overview-1")

    resp = app_client.get("/api/v1/overview", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["candidates"]["total"] >= 1
    assert body["sources"]["total"] >= 1
    assert body["sources"]["enabled"] + body["sources"]["disabled"] == body["sources"]["total"]
    assert "not_selected" in body["candidates"]["by_newsletter_status"]
    assert body["editorial_drafts"]["total"] >= 0
    assert body["generated_at"]


def test_overview_requires_authentication(app_client):
    resp = app_client.get("/api/v1/overview")
    assert resp.status_code == 401


def test_overview_accepts_service_token(app_client, service_headers):
    resp = app_client.get("/api/v1/overview", headers=service_headers)
    assert resp.status_code == 200


# --- PATCH /candidates/{id}/editorial (alias for .../editorial-draft) ---


def test_editorial_alias_reuses_editorial_draft_logic(app_client, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "editorial-alias")
    gen_resp = app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-draft", json={}, headers=admin_headers)
    assert gen_resp.status_code == 200

    patch_resp = app_client.patch(
        f"/api/v1/candidates/{candidate_id}/editorial",
        json={"headline": "Edited via CMT Veda compat route"},
        headers=reviewer_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["headline"] == "Edited via CMT Veda compat route"

    # The change is visible through the original path too -- same draft,
    # not a second copy.
    get_resp = app_client.get(f"/api/v1/candidates/{candidate_id}/editorial-draft", headers=admin_headers)
    assert get_resp.json()["headline"] == "Edited via CMT Veda compat route"


def test_editorial_alias_accepts_service_token(app_client, admin_headers, reviewer_headers, service_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "editorial-alias-service")
    app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-draft", json={}, headers=admin_headers)

    resp = app_client.patch(
        f"/api/v1/candidates/{candidate_id}/editorial", json={"summary": "Service-token edit"}, headers=service_headers
    )
    assert resp.status_code == 200


# --- POST /candidates/{id}/editorial-status ---


def test_editorial_status_happy_path_through_states(app_client, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "editorial-status-happy")

    r1 = app_client.post(
        f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "selected"}, headers=reviewer_headers
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "selected"

    # drafted is reached automatically elsewhere, not via this dispatcher
    # (mirrors mark_drafted()'s own "only from selected" guard) -- jump
    # straight from selected to under_review is not valid either, since
    # ALLOWED_TRANSITIONS[selected] = {drafted, archived}. Confirm that.
    r2 = app_client.post(
        f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "under_review"}, headers=reviewer_headers
    )
    assert r2.status_code == 409


def test_editorial_status_unsupported_value_422(app_client, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "editorial-status-bad-value")
    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "not_a_real_status"}, headers=reviewer_headers
    )
    assert resp.status_code == 422


def test_editorial_status_reviewer_blocked_from_approve(app_client, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "editorial-status-role-block")
    app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "selected"}, headers=reviewer_headers)

    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "approved"}, headers=reviewer_headers
    )
    assert resp.status_code == 403


def test_editorial_status_admin_can_approve(app_client, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "editorial-status-admin-approve")
    app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "selected"}, headers=admin_headers)
    # selected -> drafted required before under_review; simulate via direct
    # newsletter_workflow call is out of scope here -- instead confirm
    # admin CAN request 'approved' (role check passes) even though the
    # underlying transition itself still correctly 409s from 'selected'.
    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "approved"}, headers=admin_headers
    )
    # Role check passes (no 403); transition itself is invalid from
    # 'selected' (proves this endpoint doesn't bypass real workflow
    # validation just because the caller is an admin).
    assert resp.status_code == 409


def test_editorial_status_reject_requires_reason(app_client, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "editorial-status-reject-no-reason")
    app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "selected"}, headers=reviewer_headers)

    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "rejected"}, headers=reviewer_headers
    )
    assert resp.status_code == 422


def test_editorial_status_accepts_service_token(app_client, service_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "editorial-status-service")
    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "selected"}, headers=service_headers
    )
    assert resp.status_code == 200
    # service may also do admin-only transitions (trusted gateway)
    resp2 = app_client.post(
        f"/api/v1/candidates/{candidate_id}/editorial-status", json={"status": "archived"}, headers=service_headers
    )
    assert resp2.status_code == 200


# --- POST /candidates/{id}/schedule (alias), DELETE .../schedule (new) ---


def _advance_to_approved(app_client, db_session, admin_headers, reviewer_headers, candidate_id):
    """
    Drives a candidate from not_selected through to approved, purely for
    test setup. Note: generating a draft via `POST .../editorial-draft`
    does NOT itself advance the newsletter item to 'drafted' -- only the
    worker's automatic pipeline (app/worker/handlers.py::
    handle_generate_editorial_draft) calls mark_drafted() after a
    worker-driven analysis. A human calling the draft-generation endpoint
    directly (as tested here) must still be moved to 'drafted' the same
    way tests/test_newsletter_workflow.py's own tests already do it --
    there is no HTTP endpoint for this one internal step, so this helper
    reaches into db_session directly for just this step, exactly
    mirroring the established test pattern.
    """
    import uuid as uuid_module

    from app.models.candidate import DiscoveryCandidate
    from app.services.workflows import newsletter_workflow as wf

    app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/select", headers=reviewer_headers)
    app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-draft", json={}, headers=admin_headers)

    candidate = db_session.get(DiscoveryCandidate, uuid_module.UUID(candidate_id))
    wf.mark_drafted(db_session, candidate, uuid_module.uuid4())
    db_session.commit()

    app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/review", headers=reviewer_headers)
    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/approve", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_schedule_alias_reuses_existing_logic(app_client, db_session, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "schedule-alias")
    _advance_to_approved(app_client, db_session, admin_headers, reviewer_headers, candidate_id)

    scheduled_for = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/schedule", json={"scheduled_for": scheduled_for}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "scheduled"


def test_unschedule_reverts_to_approved_and_clears_scheduled_for(app_client, db_session, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "unschedule-happy")
    _advance_to_approved(app_client, db_session, admin_headers, reviewer_headers, candidate_id)

    scheduled_for = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    app_client.post(f"/api/v1/candidates/{candidate_id}/schedule", json={"scheduled_for": scheduled_for}, headers=admin_headers)

    resp = app_client.delete(f"/api/v1/candidates/{candidate_id}/schedule", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["scheduled_for"] is None


def test_unschedule_when_not_scheduled_is_409(app_client, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "unschedule-invalid")
    resp = app_client.delete(f"/api/v1/candidates/{candidate_id}/schedule", headers=admin_headers)
    assert resp.status_code == 409


def test_schedule_and_unschedule_accept_service_token(app_client, db_session, service_headers, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "schedule-service")
    _advance_to_approved(app_client, db_session, admin_headers, reviewer_headers, candidate_id)

    scheduled_for = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    resp = app_client.post(
        f"/api/v1/candidates/{candidate_id}/schedule", json={"scheduled_for": scheduled_for}, headers=service_headers
    )
    assert resp.status_code == 200

    resp2 = app_client.delete(f"/api/v1/candidates/{candidate_id}/schedule", headers=service_headers)
    assert resp2.status_code == 200


# --- POST /candidates/{id}/publish ---


def test_publish_candidate_requires_scheduled_state(app_client, db_session, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "publish-not-scheduled")
    _advance_to_approved(app_client, db_session, admin_headers, reviewer_headers, candidate_id)

    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/publish", headers=admin_headers)
    assert resp.status_code == 422


def test_publish_candidate_happy_path(app_client, db_session, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "publish-happy")
    _advance_to_approved(app_client, db_session, admin_headers, reviewer_headers, candidate_id)

    scheduled_for = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    app_client.post(f"/api/v1/candidates/{candidate_id}/schedule", json={"scheduled_for": scheduled_for}, headers=admin_headers)

    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/publish", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"

    candidate_resp = app_client.get(f"/api/v1/candidates/{candidate_id}", headers=admin_headers)
    assert candidate_resp.json()["newsletter_status"] == "published"


# --- POST /candidates/bulk ---


def test_bulk_action_partial_failure_does_not_hide_other_results(app_client, reviewer_headers):
    good_id_1 = _create_candidate(app_client, reviewer_headers, "bulk-good-1")
    good_id_2 = _create_candidate(app_client, reviewer_headers, "bulk-good-2")
    bogus_id = "00000000-0000-0000-0000-000000000000"

    resp = app_client.post(
        "/api/v1/candidates/bulk",
        json={"candidate_ids": [good_id_1, bogus_id, good_id_2], "status": "selected"},
        headers=reviewer_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 1

    by_id = {r["candidate_id"]: r for r in body["results"]}
    assert by_id[good_id_1]["success"] is True
    assert by_id[good_id_2]["success"] is True
    assert by_id[bogus_id]["success"] is False
    assert by_id[bogus_id]["error"]

    # The two good candidates were actually updated despite the bad one.
    c1 = app_client.get(f"/api/v1/candidates/{good_id_1}", headers=reviewer_headers)
    assert c1.json()["newsletter_status"] == "selected"


def test_bulk_action_rejects_unsupported_status_before_touching_any_candidate(app_client, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "bulk-bad-status")
    resp = app_client.post(
        "/api/v1/candidates/bulk", json={"candidate_ids": [candidate_id], "status": "not_real"}, headers=reviewer_headers
    )
    assert resp.status_code == 422


def test_bulk_action_reviewer_blocked_from_admin_only_status(app_client, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "bulk-role-block")
    resp = app_client.post(
        "/api/v1/candidates/bulk", json={"candidate_ids": [candidate_id], "status": "approved"}, headers=reviewer_headers
    )
    assert resp.status_code == 403


def test_bulk_action_accepts_service_token(app_client, service_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "bulk-service")
    resp = app_client.post(
        "/api/v1/candidates/bulk", json={"candidate_ids": [candidate_id], "status": "selected"}, headers=service_headers
    )
    assert resp.status_code == 200
    assert resp.json()["succeeded"] == 1


# --- POST /runs/{id}/retry ---


def test_run_retry_not_found(app_client, admin_headers):
    resp = app_client.post(
        "/api/v1/runs/00000000-0000-0000-0000-000000000000/retry", headers=admin_headers
    )
    assert resp.status_code == 404


def test_run_retry_reuses_job_queue(app_client, admin_headers, db_session):
    from app.models.run import DiscoveryRun
    from app.models.source import DiscoverySource

    source = DiscoverySource(
        source_name="Retry Test Source",
        source_type="rss",
        source_tier="tier_2",
        collection_method="rss_atom",
        configuration={"collector": "generic_rss", "feed_url": "https://example.org/feed"},
    )
    db_session.add(source)
    db_session.flush()

    run = DiscoveryRun(source_id=source.id, status="failed", run_key="retry-test-key-1")
    db_session.add(run)
    db_session.commit()

    resp = app_client.post(f"/api/v1/runs/{run.id}/retry", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"

    # Idempotent, same as /sources/{id}/run: a second immediate retry
    # returns the same in-flight job rather than creating a duplicate,
    # because it reuses the exact same trigger_run()/dedupe_key mechanism.
    resp2 = app_client.post(f"/api/v1/runs/{run.id}/retry", headers=admin_headers)
    assert resp2.status_code == 200
    assert resp2.json()["job_id"] == resp.json()["job_id"]


def test_run_retry_rejects_already_running(app_client, admin_headers, db_session):
    from app.models.run import DiscoveryRun
    from app.models.source import DiscoverySource

    source = DiscoverySource(
        source_name="Retry Running Source", source_type="rss", source_tier="tier_2",
        collection_method="rss_atom", configuration={},
    )
    db_session.add(source)
    db_session.flush()

    run = DiscoveryRun(source_id=source.id, status="running", run_key="retry-test-key-2")
    db_session.add(run)
    db_session.commit()

    resp = app_client.post(f"/api/v1/runs/{run.id}/retry", headers=admin_headers)
    assert resp.status_code == 409


def test_run_retry_requires_admin_or_service(app_client, reviewer_headers, db_session):
    from app.models.run import DiscoveryRun
    from app.models.source import DiscoverySource

    source = DiscoverySource(
        source_name="Retry Auth Source", source_type="rss", source_tier="tier_2",
        collection_method="rss_atom", configuration={},
    )
    db_session.add(source)
    db_session.flush()
    run = DiscoveryRun(source_id=source.id, status="failed", run_key="retry-test-key-3")
    db_session.add(run)
    db_session.commit()

    resp = app_client.post(f"/api/v1/runs/{run.id}/retry", headers=reviewer_headers)
    assert resp.status_code == 403


# --- Existing granular routes still work unchanged (regression check) ---


def test_existing_granular_newsletter_routes_still_work(app_client, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "regression-granular")
    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/newsletter/select", headers=reviewer_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "selected"


def test_existing_editorial_draft_route_still_works(app_client, admin_headers, reviewer_headers):
    candidate_id = _create_candidate(app_client, reviewer_headers, "regression-editorial-draft")
    resp = app_client.post(f"/api/v1/candidates/{candidate_id}/editorial-draft", json={}, headers=admin_headers)
    assert resp.status_code == 200


def test_existing_source_enable_disable_still_work(app_client, admin_headers):
    create_resp = app_client.post(
        "/api/v1/sources",
        json={
            "source_name": "Regression Source",
            "source_type": "rss",
            "source_tier": "tier_2",
            "collection_method": "rss_atom",
        },
        headers=admin_headers,
    )
    source_id = create_resp.json()["id"]
    resp = app_client.post(f"/api/v1/sources/{source_id}/disable", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
