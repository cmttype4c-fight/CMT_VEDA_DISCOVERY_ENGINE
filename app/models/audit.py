import uuid
from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, new_uuid, utcnow


class DiscoveryAuditLog(Base):
    """
    Append-only audit trail (spec #28).

    Deliberately NOT a TimestampMixin table with updated_at -- audit
    records are immutable once written. Every workflow transition,
    override, and generation event writes exactly one row here via
    app/services/audit_service.py (never edited or deleted afterwards;
    spec #52 prefers archiving over destructive deletion generally, and
    audit rows are never deleted at all).
    """

    __tablename__ = "discovery_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    action: Mapped[str] = mapped_column(String(50), nullable=False)  # enums.AuditAction
    performed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    performed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    old_value: Mapped[dict | None] = mapped_column(PortableJSON(), nullable=True)
    new_value: Mapped[dict | None] = mapped_column(PortableJSON(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Explicitly named (rather than relying on `index=True` on the
        # column, which SQLAlchemy would auto-name differently, e.g.
        # `ix_discovery_audit_log_candidate_id`) so the index name here
        # matches the hand-written Alembic migration's
        # `ix_audit_candidate_id` exactly -- found and fixed as part of
        # the Revision 3 migration-vs-models cross-check (see
        # IMPLEMENTATION_STATUS.md). Purely a naming/consistency fix; the
        # index's purpose and columns are unchanged.
        Index("ix_audit_candidate_id", "candidate_id"),
        Index("ix_audit_candidate_time", "candidate_id", "performed_at"),
        Index("ix_audit_action", "action"),
    )
