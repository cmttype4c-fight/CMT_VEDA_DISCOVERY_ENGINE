"""
Worker job handlers (spec #31).

`collect_source` runs the full Collection -> Normalize -> Deduplicate ->
Candidate pipeline for one source in one pass, per-record, for efficiency
(a source run can easily involve hundreds of records; a job-per-record
pipeline would be far more Postgres round-trips for no practical benefit
at v1 scale). `normalize_record` / `deduplicate_record` are still
implemented and dispatchable as standalone job types -- useful for
reprocessing an individual already-collected raw record (e.g. after a
normalization bug-fix) -- satisfying spec #31's listed job type set
without forcing an inefficient per-record queue for the common path.

A failure partway through one source's records does not abort the run
(spec #13: "A failure from one source must not stop other sources" --
and by extension, one bad record within a source):

    bad record -> record error recorded -> next record continues -> source run does not abort

Each record's DB work is wrapped in its own SAVEPOINT
(`db.begin_nested()`), not a full session rollback. If a record fails,
SQLAlchemy rolls back only to that SAVEPOINT -- undoing just that
record's partial work -- while everything committed for earlier records,
and the `run`/`source` objects the rest of the function keeps using
afterwards, are completely unaffected. This is deliberately NOT
implemented as "catch the exception and call `session.rollback()`":
a full rollback expires every object in the session (spec-compliant per
SQLAlchemy, but needlessly disruptive here) and offers no isolation
benefit over a SAVEPOINT for this use case. Source-level failures (e.g.
the collector itself can't reach the API at all) are handled by the
separate outer `except CollectorError` below and are inherently isolated
from other sources already, since each source's `collect_source` job runs
in its own worker-claimed job with its own database session
(see app/worker/worker.py's `session_scope()` per job).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.base import CollectorError
from app.collectors.registry import build_collector
from app.models.candidate import DiscoveryCandidate
from app.models.enums import RecordDedupeStatus, RunStatus
from app.models.job import DiscoveryJob
from app.models.run import DiscoveryRun
from app.models.source import DiscoverySource
from app.models.source_record import DiscoverySourceRecord
from app.services import candidate_service
from app.services.deduplication import deduplicate
from app.services.intelligence.editorial_service import generate_editorial_draft
from app.services.intelligence.veda_intelligence import analyse_candidate as run_analysis
from app.services.normalization import normalize_to_source_record
from app.services.workflows.newsletter_workflow import mark_drafted
from app.models.newsletter import NewsletterItem
from app.worker.job_queue import enqueue
from app.logging_config import get_logger

logger = get_logger(component="worker")


async def handle_collect_source(db: Session, job: DiscoveryJob) -> dict:
    source_id = uuid.UUID(job.payload["source_id"])
    source = db.get(DiscoverySource, source_id)
    if source is None:
        raise ValueError(f"Source {source_id} not found")
    if not source.enabled:
        return {"skipped": True, "reason": "source disabled"}

    run = DiscoveryRun(
        source_id=source.id,
        status=RunStatus.running.value,
        started_at=datetime.now(timezone.utc),
        run_key=str(job.id),
    )
    db.add(run)
    db.flush()

    since = None if job.payload.get("historical_import") else source.last_success_at

    stats = {"records_found": 0, "new_records": 0, "duplicates": 0, "updated_records": 0, "candidates_created": 0}
    error_details: list[dict] = []

    try:
        collector = build_collector(source)
        async for normalized in collector.collect(since=since):
            stats["records_found"] += 1
            record_outcome = None
            created_candidate = False

            try:
                # A SAVEPOINT (nested transaction), not a full session
                # rollback, isolates this one record's failure. If
                # anything inside this block raises, SQLAlchemy rolls
                # back only to the SAVEPOINT -- undoing just this
                # record's partial work -- and re-raises the original
                # exception to the `except` below. Critically, this does
                # NOT expire/invalidate `run`, `source`, or anything
                # committed for earlier records the way a full
                # `session.rollback()` would, so the loop can safely
                # continue to the next record and the outer commit/finally
                # logic can safely keep using `run`/`source` afterwards.
                with db.begin_nested():
                    source_record = normalize_to_source_record(
                        normalized, source_id=source.id, source_type=source.source_type, run_id=run.id
                    )
                    result = deduplicate(db, source_record)
                    record_outcome = result.status

                    if result.status == RecordDedupeStatus.new:
                        candidate = candidate_service.create_candidate_from_source_record(db, result.record, source)
                        created_candidate = True
                        enqueue(
                            db,
                            job_type="analyse_candidate",
                            payload={"candidate_id": str(candidate.id)},
                            dedupe_key=f"analyse_candidate:{candidate.id}",
                        )
                    elif result.status == RecordDedupeStatus.updated:
                        candidate = (
                            db.get(DiscoveryCandidate, result.matched_existing.candidate_id)
                            if result.matched_existing
                            else None
                        )
                        if candidate:
                            candidate_service.refresh_candidate_from_updated_record(db, candidate, result.record)
                            enqueue(
                                db,
                                job_type="analyse_candidate",
                                payload={"candidate_id": str(candidate.id)},
                                dedupe_key=f"analyse_candidate:{candidate.id}",
                            )

                # Reached only if the SAVEPOINT above committed cleanly --
                # safe to reflect its outcome in the run-level stats now.
                # (Incrementing `stats` *inside* the nested block would be
                # wrong: a Python-level counter increment isn't undone by
                # a SAVEPOINT rollback, so a partial failure partway
                # through the block could otherwise inflate the stats for
                # work that was actually rolled back.)
                if record_outcome == RecordDedupeStatus.new:
                    stats["new_records"] += 1
                    if created_candidate:
                        stats["candidates_created"] += 1
                elif record_outcome == RecordDedupeStatus.updated:
                    stats["updated_records"] += 1
                else:
                    stats["duplicates"] += 1

                db.commit()
            except Exception as record_exc:  # noqa: BLE001 - isolate per-record failures
                error_details.append({"error": str(record_exc), "external_id": getattr(normalized, "external_id", None)})
                logger.warning("record_processing_failed", source_id=str(source.id), error=str(record_exc))

        run.status = RunStatus.partial.value if error_details else RunStatus.completed.value
        source.last_success_at = datetime.now(timezone.utc)
        source.last_error = None

    except CollectorError as exc:
        run.status = RunStatus.failed.value
        error_details.append({"error": str(exc)})
        source.last_error = str(exc)
        logger.error("source_collection_failed", source_id=str(source.id), error=str(exc))

    finally:
        run.completed_at = datetime.now(timezone.utc)
        run.records_found = stats["records_found"]
        run.new_records = stats["new_records"]
        run.duplicates = stats["duplicates"]
        run.updated_records = stats["updated_records"]
        run.candidates_created = stats["candidates_created"]
        run.errors = len(error_details)
        run.error_details = error_details
        source.last_run_at = datetime.now(timezone.utc)
        db.commit()

    return {"run_id": str(run.id), **stats, "errors": len(error_details)}


async def handle_normalize_record(db: Session, job: DiscoveryJob) -> dict:
    """Reprocess-a-single-record path (see module docstring)."""
    from app.collectors.base import NormalizedRecord

    raw = job.payload["raw_record"]
    source_id = uuid.UUID(job.payload["source_id"])
    source = db.get(DiscoverySource, source_id)
    normalized = NormalizedRecord(**raw)
    source_record = normalize_to_source_record(normalized, source_id=source.id, source_type=source.source_type)
    db.add(source_record)
    db.commit()
    return {"source_record_id": str(source_record.id)}


async def handle_deduplicate_record(db: Session, job: DiscoveryJob) -> dict:
    record_id = uuid.UUID(job.payload["source_record_id"])
    record = db.get(DiscoverySourceRecord, record_id)
    if record is None:
        raise ValueError(f"Source record {record_id} not found")
    result = deduplicate(db, record)
    db.commit()
    return {"status": result.status.value}


async def handle_analyse_candidate(db: Session, job: DiscoveryJob) -> dict:
    candidate_id = uuid.UUID(job.payload["candidate_id"])
    candidate = db.get(DiscoveryCandidate, candidate_id)
    if candidate is None:
        raise ValueError(f"Candidate {candidate_id} not found")

    analysis = await run_analysis(db, candidate)
    candidate_service.apply_analysis_to_candidate(db, candidate, analysis)
    db.commit()

    enqueue(
        db,
        job_type="generate_editorial_draft",
        payload={"candidate_id": str(candidate.id)},
        dedupe_key=f"generate_editorial_draft:{candidate.id}:{analysis.analysis_version}",
    )
    db.commit()
    return {"analysis_id": str(analysis.id), "cmt_relevance_score": analysis.cmt_relevance_score}


async def handle_generate_editorial_draft(db: Session, job: DiscoveryJob) -> dict:
    candidate_id = uuid.UUID(job.payload["candidate_id"])
    candidate = db.get(DiscoveryCandidate, candidate_id)
    if candidate is None:
        raise ValueError(f"Candidate {candidate_id} not found")

    draft = await generate_editorial_draft(db, candidate)

    existing_item = db.execute(
        select(NewsletterItem).where(NewsletterItem.candidate_id == candidate.id)
    ).scalars().first()
    if existing_item and existing_item.status == "selected":
        mark_drafted(db, candidate, draft.id)

    db.commit()
    return {"draft_id": str(draft.id), "draft_version": draft.draft_version}


async def handle_maintenance(db: Session, job: DiscoveryJob) -> dict:
    from app.worker.job_queue import recover_stuck_jobs

    task = job.payload.get("task", "recover_stuck_jobs")
    if task == "recover_stuck_jobs":
        recovered = recover_stuck_jobs(db)
        db.commit()
        return {"recovered": recovered}
    return {"skipped": True, "reason": f"unknown maintenance task {task!r}"}


DISPATCH = {
    "collect_source": handle_collect_source,
    "normalize_record": handle_normalize_record,
    "deduplicate_record": handle_deduplicate_record,
    "analyse_candidate": handle_analyse_candidate,
    "generate_editorial_draft": handle_generate_editorial_draft,
    "maintenance": handle_maintenance,
}
