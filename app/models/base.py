"""
Portable base types shared by every model.

Design goal (per spec item #4 / #55): the schema must remain
"PostgreSQL-portable" -- i.e. it is designed for and targets PostgreSQL in
production, but the same SQLAlchemy models can run against SQLite in tests
so the full pipeline can be exercised locally without a running Postgres
instance or network access.

- GUID: native UUID on Postgres, CHAR(36) string on everything else.
- PortableJSON: JSONB on Postgres, JSON everywhere else.
- TimestampMixin: created_at/updated_at as timezone-aware timestamps.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator, CHAR
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class GUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL's native UUID type when available, otherwise stores as a
    36-character string (SQLite, used only for local/unit testing).
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            return str(uuid.UUID(str(value)))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class PortableJSON(TypeDecorator):
    """JSONB on Postgres, plain JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
