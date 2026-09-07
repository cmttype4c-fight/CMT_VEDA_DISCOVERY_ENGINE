"""Discovery run API (spec #36)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth import Principal, require_any_authenticated
from app.models.run import DiscoveryRun
from app.schemas.common import Page
from app.schemas.run import RunOut

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
