"""Audit log API (spec #28, #36)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth import Principal, require_any_authenticated
from app.models.audit import DiscoveryAuditLog
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
def list_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    candidate_id: uuid.UUID | None = None,
    action: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_any_authenticated),
):
    query = select(DiscoveryAuditLog)
    if candidate_id:
        query = query.where(DiscoveryAuditLog.candidate_id == candidate_id)
    if action:
        query = query.where(DiscoveryAuditLog.action == action)

    total = len(db.execute(query).scalars().all())
    rows = db.execute(
        query.order_by(DiscoveryAuditLog.performed_at.desc()).limit(limit).offset(offset)
    ).scalars().all()
    return Page(items=rows, total=total, limit=limit, offset=offset, has_more=offset + len(rows) < total)
