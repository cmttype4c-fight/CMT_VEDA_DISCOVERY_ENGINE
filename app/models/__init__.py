"""
Import every model module here so that (a) Alembic's `target_metadata =
Base.metadata` sees the full schema for autogenerate, and (b)
`Base.metadata.create_all()` in tests creates every table.
"""
from app.models.base import Base  # noqa: F401

from app.models.source import DiscoverySource  # noqa: F401
from app.models.run import DiscoveryRun  # noqa: F401
from app.models.source_record import DiscoverySourceRecord  # noqa: F401
from app.models.candidate import DiscoveryCandidate  # noqa: F401
from app.models.analysis import DiscoveryAnalysis  # noqa: F401
from app.models.editorial import DiscoveryEditorialDraft  # noqa: F401
from app.models.newsletter import NewsletterItem, NewsletterPublication  # noqa: F401
from app.models.rag import RagIngestionRequest  # noqa: F401
from app.models.taxonomy import DiscoveryTaxonomy  # noqa: F401
from app.models.audit import DiscoveryAuditLog  # noqa: F401
from app.models.job import DiscoveryJob  # noqa: F401
from app.models.document import DiscoveryDocument  # noqa: F401

__all__ = [
    "Base",
    "DiscoverySource",
    "DiscoveryRun",
    "DiscoverySourceRecord",
    "DiscoveryCandidate",
    "DiscoveryAnalysis",
    "DiscoveryEditorialDraft",
    "NewsletterItem",
    "NewsletterPublication",
    "RagIngestionRequest",
    "DiscoveryTaxonomy",
    "DiscoveryAuditLog",
    "DiscoveryJob",
    "DiscoveryDocument",
]
