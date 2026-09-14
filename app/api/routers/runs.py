"""Discovery run API (spec #36)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth import Principal, require_any_authenticated, require_service_or_admin
from app.models.run import DiscoveryRun
from app.models.source import DiscoverySource
from app.schemas.common import Page
from app.schemas.run import RunOut
from app.schemas.source import SourceRunTriggerResponse
from app.worker.scheduler import trigger_run

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=Page[RunOut])
def list_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    query = select(DiscoveryRun)
    if source_id:
        query = query.where(DiscoveryRun.source_id == source_id)
    if status_filter:
        query = query.where(DiscoveryRun.status == status_filter)

    total = len(db.execute(query).scalars().all())
    rows = db.execute(
        query.order_by(DiscoveryRun.created_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return Page(items=rows, total=total, limit=limit, offset=offset, has_more=offset + len(rows) < total)


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db), _: Principal = Depends(require_any_authenticated)):
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    return run


@router.post("/{run_id}/retry", response_model=SourceRunTriggerResponse)
def retry_run(
    run_id: uuid.UUID,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_service_or_admin),
):
    """
    CMT Veda compatibility: re-triggers collection for a failed/partial
    run's source. Does not execute collection inline or create a second
    job queue -- reuses `trigger_run()`, the exact function
    `POST /sources/{id}/run` already calls, which enqueues a
    `collect_source` job for the worker and is idempotent/dedupe-safe via
    the source's `dedupe_key` (spec #34). Response shape matches
    `POST /sources/{id}/run` for the same reason: a fresh
    `DiscoveryRun` row doesn't exist yet at request time (the worker
    creates it when the job is claimed), so -- consistent with the
    existing endpoint's own behavior, unchanged here -- `run_id` in the
    response is the newly (re)triggered job's id, not `run_id` from the
    path.
    """
    run = db.get(DiscoveryRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Run {run_id} not found")
    if run.status in ("queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Run {run_id} is currently '{run.status}'; wait for it to finish before retrying.",
        )

    source = db.get(DiscoverySource, run.source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Source for run {run_id} not found")

    job = trigger_run(db, source)
    db.commit()
    return SourceRunTriggerResponse(run_id=job.id, job_id=job.id, status=job.status)
