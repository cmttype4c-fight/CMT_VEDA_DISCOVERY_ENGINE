"""Source registry API (spec #36)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_source_or_404
from app.auth import Principal, require_admin, require_any_authenticated
from app.models.source import DiscoverySource
from app.schemas.common import Page
from app.schemas.source import SourceCreate, SourceOut, SourceRunTriggerResponse, SourceUpdate
from app.worker.scheduler import trigger_run

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=Page[SourceOut])
def list_sources(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    enabled: bool | None = None,
    source_type: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    query = select(DiscoverySource)
    if enabled is not None:
        query = query.where(DiscoverySource.enabled == enabled)
    if source_type:
        query = query.where(DiscoverySource.source_type == source_type)

    total = len(db.execute(query).scalars().all())
    rows = db.execute(query.order_by(DiscoverySource.source_name).limit(limit).offset(offset)).scalars().all()
    return Page(items=rows, total=total, limit=limit, offset=offset, has_more=offset + len(rows) < total)


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source: DiscoverySource = Depends(get_source_or_404), _: Principal = Depends(require_any_authenticated)):
    return source


@router.post("", response_model=SourceOut, status_code=201)
def create_source(payload: SourceCreate, db: Session = Depends(get_db), _: Principal = Depends(require_admin)):
    source = DiscoverySource(**payload.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.patch("/{source_id}", response_model=SourceOut)
def update_source(
    payload: SourceUpdate,
    source: DiscoverySource = Depends(get_source_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
):
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field_name, value)
    db.commit()
    db.refresh(source)
    return source


@router.post("/{source_id}/enable", response_model=SourceOut)
def enable_source(
    source: DiscoverySource = Depends(get_source_or_404), db: Session = Depends(get_db), _: Principal = Depends(require_admin)
):
    source.enabled = True
    db.commit()
    db.refresh(source)
    return source


@router.post("/{source_id}/disable", response_model=SourceOut)
def disable_source(
    source: DiscoverySource = Depends(get_source_or_404), db: Session = Depends(get_db), _: Principal = Depends(require_admin)
):
    source.enabled = False
    db.commit()
    db.refresh(source)
    return source


@router.post("/{source_id}/run", response_model=SourceRunTriggerResponse)
def run_source_now(
    historical_import: bool = Query(False),
    source: DiscoverySource = Depends(get_source_or_404),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_admin),
):
    """'Run now' backend operation (spec #33). Idempotent/duplicate-run-safe
    (spec #34) via the job queue's dedupe_key."""
    job = trigger_run(db, source, historical_import=historical_import)
    db.commit()
    return SourceRunTriggerResponse(run_id=job.id, job_id=job.id, status=job.status)
