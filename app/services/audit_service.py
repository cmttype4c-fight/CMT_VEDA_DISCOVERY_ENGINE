"""
Audit logging service (spec #28). Every write goes through here so the
log format stays consistent and no code path can silently skip auditing
a state-changing action.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import DiscoveryAuditLog
from app.models.enums import AuditAction


def write_audit_log(
    db: Session,
    *,
    action: AuditAction,
    performed_by: str,
    candidate_id: uuid.UUID | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    notes: str | None = None,
) -> DiscoveryAuditLog:
    entry = DiscoveryAuditLog(
        candidate_id=candidate_id,
        action=action.value if isinstance(action, AuditAction) else action,
        performed_by=performed_by,
        old_value=old_value,
        new_value=new_value,
        notes=notes,
    )
    db.add(entry)
    db.flush()
    return entry
