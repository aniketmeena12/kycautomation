"""
Shared mixins for ORM models.

TimestampMixin: row-level created/updated bookkeeping, independent of the
    source data's own timestamps.
ProvenanceMixin: every record ingested from a Phase 0 dataset carries where
    it came from. This is the concrete mechanism behind "never silently merge
    curated demo records with authoritative records without provenance" --
    source_tier and source_type are queryable, filterable columns, not an
    afterthought in a free-text note.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import SourceTier, SourceType


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ProvenanceMixin:
    """source_dataset: the literal file this record was ingested from, e.g.
    'kyc_profiles/clients_with_fatf_ofac.csv' -- always the canonical path
    per docs/phase-0-dataset-audit.md SS3 (never the duplicate root-level copy).
    """

    source_dataset: Mapped[str] = mapped_column(nullable=False)
    source_tier: Mapped[SourceTier] = mapped_column(SAEnum(SourceTier), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
