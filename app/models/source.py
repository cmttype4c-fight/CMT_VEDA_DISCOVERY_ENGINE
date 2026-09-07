import uuid

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class DiscoverySource(Base, TimestampMixin):
    """
    Configurable source registry entry (spec #5, #6).

    The engine must support adding future collectors without redesigning the
    database -- `source_type`/`collection_method` select which collector
    implementation handles this source (see app/collectors/registry.py),
    and `configuration` carries collector-specific settings (vocabulary,
    query params, feed URL, etc.) without needing new columns per source.
    """

    __tablename__ = "discovery_sources"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)

    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # enums.SourceType
    source_tier: Mapped[str] = mapped_column(String(20), nullable=False)  # enums.SourceTier
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    collection_method: Mapped[str] = mapped_column(String(50), nullable=False)  # enums.CollectionMethod

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    frequency: Mapped[str] = mapped_column(String(50), nullable=False, default="daily")  # cron-like or "daily"/"weekly"

    last_run_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Collector-specific config: PubMed vocabulary, feed URLs, API params,
    # historical-import window, etc. Keeps the registry extensible (spec #5).
    configuration: Mapped[dict] = mapped_column(PortableJSON(), nullable=False, default=dict)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_discovery_sources_type_enabled", "source_type", "enabled"),
        Index("ix_discovery_sources_tier", "source_tier"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DiscoverySource {self.source_name} ({self.source_type})>"
