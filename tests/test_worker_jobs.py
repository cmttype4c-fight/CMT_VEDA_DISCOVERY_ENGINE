from datetime import datetime, timedelta, timezone

from app.worker.job_queue import claim_next_job, complete_job, enqueue, fail_job, recover_stuck_jobs


def test_enqueue_is_idempotent_via_dedupe_key(db_session):
    job1 = enqueue(db_session, job_type="collect_source", payload={"source_id": "abc"}, dedupe_key="collect_source:abc")
    job2 = enqueue(db_session, job_type="collect_source", payload={"source_id": "abc"}, dedupe_key="collect_source:abc")
    assert job1.id == job2.id  # spec #34/#49: no duplicate concurrent job for the same source


def test_enqueue_without_dedupe_key_allows_multiple(db_session):
    job1 = enqueue(db_session, job_type="maintenance", payload={})
    job2 = enqueue(db_session, job_type="maintenance", payload={})
    assert job1.id != job2.id


def test_claim_next_job_marks_running_and_increments_attempts(db_session):
    job = enqueue(db_session, job_type="maintenance", payload={})
    claimed = claim_next_job(db_session, worker_id="test-worker-1")
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.locked_by == "test-worker-1"


def test_claim_next_job_respects_available_at(db_session):
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    enqueue(db_session, job_type="maintenance", payload={}, available_at=future)
    claimed = claim_next_job(db_session, worker_id="test-worker-1")
    assert claimed is None


def test_fail_job_retries_with_backoff_then_dead_letters(db_session):
    job = enqueue(db_session, job_type="maintenance", payload={}, max_attempts=2)

    claimed = claim_next_job(db_session, worker_id="w1")
    fail_job(db_session, claimed, "transient error")
    assert claimed.status == "queued"  # attempt 1 of 2 -> retry
    assert claimed.available_at > datetime.now(timezone.utc)

    # Force it available now for the test, simulate second attempt.
    claimed.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.flush()

    reclaimed = claim_next_job(db_session, worker_id="w1")
    assert reclaimed.attempts == 2
    fail_job(db_session, reclaimed, "still failing")
    assert reclaimed.status == "dead_letter"  # exceeded max_attempts -- spec #32: not retried forever


def test_complete_job_marks_completed(db_session):
    job = enqueue(db_session, job_type="maintenance", payload={})
    claimed = claim_next_job(db_session, worker_id="w1")
    complete_job(db_session, claimed)
    assert claimed.status == "completed"
    assert claimed.completed_at is not None


def test_recover_stuck_jobs_requeues_stale_locks(db_session):
    job = enqueue(db_session, job_type="maintenance", payload={})
    claimed = claim_next_job(db_session, worker_id="crashed-worker")
    # Simulate the lock going stale (worker crashed 1 hour ago).
    claimed.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db_session.flush()

    recovered_count = recover_stuck_jobs(db_session, stuck_after_minutes=30)
    assert recovered_count == 1
    assert claimed.status == "queued"
    assert claimed.locked_by is None


async def test_one_bad_record_does_not_abort_the_whole_source_run(db_session, monkeypatch):
    """spec #13: a failure from one source (and by extension one bad
    record within a source) must not stop processing of the rest."""
    import uuid

    from app.models.job import DiscoveryJob
    from app.models.source import DiscoverySource
    from app.worker.handlers import handle_collect_source

    source = DiscoverySource(
        source_name="Flaky RSS", source_type="rss", source_tier="tier_2",
        collection_method="rss_atom", configuration={"collector": "generic_rss", "feed_url": "https://example.org/feed"},
    )
    db_session.add(source)
    db_session.flush()

    from app.collectors.base import NormalizedRecord

    # Title uses the explicit Charcot-Marie-Tooth full form (not just the
    # bare "CMT" acronym) so it still clears the FINAL CORRECTIVE PROMPT's
    # stricter eligibility gate -- this test is about a bad record not
    # aborting the whole run, not about eligibility semantics.
    good_record = NormalizedRecord(
        external_id="good-1", title="A fine Charcot-Marie-Tooth disease article", canonical_url="https://example.org/good"
    )
    bad_record = NormalizedRecord(external_id=None, title=None)  # will raise when treated as NEW (no external_id)

    class FlakyCollector:
        collection_method = "rss_atom"

        def __init__(self, source, http_client=None):
            pass

        async def collect(self, since=None):
            yield good_record
            yield bad_record

    monkeypatch.setattr("app.worker.handlers.build_collector", lambda src, http_client=None: FlakyCollector(src))

    job = DiscoveryJob(job_type="collect_source", status="running", payload={"source_id": str(source.id)}, available_at=datetime.now(timezone.utc))
    db_session.add(job)
    db_session.flush()

    result = await handle_collect_source(db_session, job)

    # The good record still produced a candidate despite the bad one.
    assert result["records_found"] == 2
    assert result["candidates_created"] >= 1


# --- discovery_jobs.dedupe_key concurrency-safe DB constraint (targeted fix) ---


def test_db_rejects_duplicate_active_jobs_for_same_dedupe_key(db_session):
    """
    Directly verifies the DATABASE-level constraint itself -- not just
    enqueue()'s application-level check-then-insert -- by bypassing
    enqueue() and inserting two 'queued' rows with the same dedupe_key
    straight through the ORM. Before this fix, only a plain (non-unique)
    index existed here, so this second insert would have succeeded;
    the partial unique index (`uq_jobs_dedupe_key_active`,
    app/models/job.py) now makes that impossible at the database level,
    which is what actually protects against two simultaneous requests
    both passing an application-level check before either has inserted.
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models.job import DiscoveryJob

    job1 = DiscoveryJob(
        job_type="maintenance", status="queued", payload={}, dedupe_key="dup-key",
        available_at=datetime.now(timezone.utc),
    )
    db_session.add(job1)
    db_session.commit()

    job2 = DiscoveryJob(
        job_type="maintenance", status="queued", payload={}, dedupe_key="dup-key",
        available_at=datetime.now(timezone.utc),
    )
    db_session.add(job2)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_completed_job_does_not_block_new_active_job_with_same_dedupe_key(db_session):
    """
    Confirms the index is genuinely PARTIAL (scoped to queued/running),
    not a blanket unique constraint on dedupe_key: once a prior job with
    a given dedupe_key is no longer active, enqueuing a new active job
    with the SAME dedupe_key must still succeed -- this is exactly the
    real usage pattern (app/worker/scheduler.py reuses
    `collect_source:{source_id}` as the dedupe_key on every scheduled
    trigger for a source, relying on completed/failed runs not blocking
    future ones).
    """
    first = enqueue(db_session, job_type="collect_source", payload={}, dedupe_key="reusable-key")
    complete_job(db_session, first)

    second = enqueue(db_session, job_type="collect_source", payload={}, dedupe_key="reusable-key")
    assert second.id != first.id
    assert second.status == "queued"


def test_enqueue_recovers_gracefully_from_lost_dedupe_race(db_session, monkeypatch):
    """
    Simulates two callers racing to enqueue the same dedupe_key: by the
    time the second caller's INSERT actually happens, the first caller's
    job already exists and is active, but the second caller's own
    pre-insert SELECT check (the fast path) is forced to miss it --
    exactly the race enqueue()'s application-level check alone cannot
    prevent. This exercises the real recovery path: the DB constraint
    rejects the second INSERT, enqueue() catches the IntegrityError, and
    returns the actual winner rather than raising or creating a
    duplicate.
    """
    from app.worker import job_queue as job_queue_module

    winner = enqueue(db_session, job_type="collect_source", payload={}, dedupe_key="race-key")

    call_count = {"n": 0}
    original_finder = job_queue_module._find_active_job_by_dedupe_key

    def flaky_finder(db, dedupe_key):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # simulate the pre-insert check missing the winner
        return original_finder(db, dedupe_key)

    monkeypatch.setattr(job_queue_module, "_find_active_job_by_dedupe_key", flaky_finder)

    recovered = enqueue(db_session, job_type="collect_source", payload={}, dedupe_key="race-key")

    assert recovered.id == winner.id  # recovered the real winner, not a duplicate or an exception
    assert call_count["n"] == 2  # missed once (pre-insert), found it on the post-conflict re-query

    from sqlalchemy import select

    from app.models.job import DiscoveryJob

    active_jobs = db_session.execute(
        select(DiscoveryJob).where(DiscoveryJob.dedupe_key == "race-key", DiscoveryJob.status == "queued")
    ).scalars().all()
    assert len(active_jobs) == 1  # never actually created a duplicate row


def test_enqueue_still_works_normally_inside_a_savepoint(db_session):
    """
    enqueue() now uses its own SAVEPOINT internally (db.begin_nested())
    so it's safe to call from within a caller's own nested transaction --
    e.g. the worker's per-record isolation in handle_collect_source. This
    confirms a normal (non-racing) enqueue() call still works correctly
    when invoked from inside an outer db.begin_nested() block.
    """
    with db_session.begin_nested():
        job = enqueue(db_session, job_type="analyse_candidate", payload={"candidate_id": "x"}, dedupe_key="nested-key")

    assert job.status == "queued"
    assert job.dedupe_key == "nested-key"


# --- CMT eligibility gate wiring into handle_collect_source (Phase 2C) ---
# Test list items 7-9: non-CMT record does not create a candidate,
# CMT-specific record does, and a rejected record is still retained.


async def test_worker_rejects_non_cmt_record_but_retains_it_for_provenance(db_session, monkeypatch):
    from app.models.job import DiscoveryJob
    from app.models.source import DiscoverySource
    from app.services.taxonomy_service import seed_default_taxonomy
    from app.worker.handlers import handle_collect_source
    from app.collectors.base import NormalizedRecord

    seed_default_taxonomy(db_session)

    source = DiscoverySource(
        source_name="Test RSS", source_type="rss", source_tier="tier_2",
        collection_method="rss_atom", configuration={"collector": "generic_rss", "feed_url": "https://example.org/feed"},
    )
    db_session.add(source)
    db_session.flush()

    # Deliberately off-topic -- no CMT disease/subtype/gene evidence at all.
    off_topic = NormalizedRecord(
        external_id="off-topic-1",
        title="Machine learning-assisted detection of canine mammary tumors using serum autoantibody signatures",
        abstract="We trained a classifier on serum autoantibody profiles to detect mammary tumors in dogs.",
        canonical_url="https://example.org/off-topic-1",
    )

    class SingleRecordCollector:
        collection_method = "rss_atom"

        def __init__(self, source, http_client=None):
            pass

        async def collect(self, since=None):
            yield off_topic

    monkeypatch.setattr("app.worker.handlers.build_collector", lambda src, http_client=None: SingleRecordCollector(src))

    job = DiscoveryJob(
        job_type="collect_source", status="running", payload={"source_id": str(source.id)},
        available_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()

    result = await handle_collect_source(db_session, job)

    assert result["new_records"] == 1
    assert result["candidates_created"] == 0
    assert result["cmt_rejected"] == 1

    from sqlalchemy import select

    from app.models.source_record import DiscoverySourceRecord

    stored = db_session.execute(
        select(DiscoverySourceRecord).where(DiscoverySourceRecord.external_id == "off-topic-1")
    ).scalars().first()
    assert stored is not None  # test list item 9: retained for provenance/dedup
    assert stored.candidate_id is None  # test list item 7: no candidate was created
    assert stored.cmt_eligible is False
    assert stored.eligibility_reason is not None


async def test_worker_creates_candidate_for_cmt_specific_record(db_session, monkeypatch):
    """Test list item 8: CMT-specific source record creates a candidate."""
    from app.models.job import DiscoveryJob
    from app.models.source import DiscoverySource
    from app.services.taxonomy_service import seed_default_taxonomy
    from app.worker.handlers import handle_collect_source
    from app.collectors.base import NormalizedRecord

    seed_default_taxonomy(db_session)

    source = DiscoverySource(
        source_name="Test RSS 2", source_type="rss", source_tier="tier_2",
        collection_method="rss_atom", configuration={"collector": "generic_rss", "feed_url": "https://example.org/feed2"},
    )
    db_session.add(source)
    db_session.flush()

    on_topic = NormalizedRecord(
        external_id="on-topic-1",
        title="Natural history of Charcot-Marie-Tooth disease type 1A",
        abstract="A longitudinal cohort study of CMT1A disease progression.",
        canonical_url="https://example.org/on-topic-1",
    )

    class SingleRecordCollector:
        collection_method = "rss_atom"

        def __init__(self, source, http_client=None):
            pass

        async def collect(self, since=None):
            yield on_topic

    monkeypatch.setattr("app.worker.handlers.build_collector", lambda src, http_client=None: SingleRecordCollector(src))

    job = DiscoveryJob(
        job_type="collect_source", status="running", payload={"source_id": str(source.id)},
        available_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.flush()

    result = await handle_collect_source(db_session, job)

    assert result["candidates_created"] == 1
    assert result["cmt_rejected"] == 0

    from sqlalchemy import select

    from app.models.source_record import DiscoverySourceRecord

    stored = db_session.execute(
        select(DiscoverySourceRecord).where(DiscoverySourceRecord.external_id == "on-topic-1")
    ).scalars().first()
    assert stored.candidate_id is not None
    assert stored.cmt_eligible is True
