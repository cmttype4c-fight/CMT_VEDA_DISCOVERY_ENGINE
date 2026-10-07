import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import DateTime

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class NewsletterItem(Base, TimestampMixin):
    """
    Backend state machine for a candidate's newsletter journey (spec #22).

    Authoritative status lives here; `DiscoveryCandidate.newsletter_status`
    is a denormalized read copy kept in sync by
    app/services/workflows/newsletter_workflow.py, which is the ONLY code
    path allowed to change `status` (spec #50 -- no generic PATCH).
    """

    __tablename__ = "newsletter_items"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("discovery_candidates.id"), nullable=False, unique=True)
    editorial_draft_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("discovery_editorial_drafts.id"), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_selected")  # enums.NewsletterStatus

    selected_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reviewed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rejected_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # CMT Veda final-contract pass (spec item 9 -- "persistent Newsletter
    # distribution: the selected Newsletter section/destination must not
    # exist only in browser state"). Free-text editorial placement (e.g.
    # "Research Digest", "Community Spotlight") set via
    # newsletter_workflow.set_section() -- a pure metadata assignment, not
    # a state-machine transition, so it can be set/changed at any status
    # and never participates in ALLOWED_TRANSITIONS. Persisted here so it
    # survives a page refresh/different device, per the spec's complaint.
    section: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # CMT Veda final-contract pass (spec item 10 -- "published-content API
    # ... newest-first ordering"). Set once, in newsletter_workflow.publish(),
    # the moment an item actually reaches `published`. Deliberately a
    # dedicated column rather than reusing TimestampMixin.updated_at:
    # updated_at changes on ANY later edit to this row (e.g. a `section`
    # change after publication), which would silently reorder the public
    # feed -- published_at is set exactly once and never touched again.
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_newsletter_items_status", "status"),
        Index("ix_newsletter_items_published_at", "published_at"),
    )


class NewsletterPublication(Base, TimestampMixin):
    """
    An actual published (or scheduled) newsletter issue, and the items it
    contains (spec #22: newsletter_item and newsletter_publication are
    kept as separate concepts -- an item can be scheduled into a
    publication before that publication actually goes out).
    """

    __tablename__ = "newsletter_publications"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")  # scheduled|published|archived
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Ordered list of newsletter_item IDs (as strings) included in this
    # publication, plus any publication-level metadata/notes.
    item_ids: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    publication_metadata: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)

    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        Index("ix_newsletter_pub_status", "status"),
    )
