import uuid

from sqlalchemy import Boolean, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, GUID, PortableJSON, TimestampMixin, new_uuid


class DiscoveryTaxonomy(Base, TimestampMixin):
    """
    Admin-extendable controlled vocabulary (spec #9): genes, CMT subtypes,
    disease-name synonyms, topics, etc. This is intentionally data, not
    code, so administrators can extend it (e.g. add a newly-described gene)
    without a deployment.

    `category` groups entries (e.g. "gene", "cmt_subtype", "topic",
    "disease_synonym"); `term` is the canonical value; `synonyms` supports
    matching alternate spellings/acronyms found in source text (e.g.
    "HMSN" -> "hereditary motor sensory neuropathy").
    """

    __tablename__ = "discovery_taxonomy"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=new_uuid)

    category: Mapped[str] = mapped_column(String(50), nullable=False)
    term: Mapped[str] = mapped_column(String(255), nullable=False)
    synonyms: Mapped[list] = mapped_column(PortableJSON(), nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    taxonomy_version: Mapped[str] = mapped_column(String(50), nullable=False, default="v1")

    __table_args__ = (
        UniqueConstraint("category", "term", name="uq_taxonomy_category_term"),
        Index("ix_taxonomy_category", "category"),
    )
