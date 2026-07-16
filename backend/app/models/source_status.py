"""
DatasetSourceStatus -- dynamic, database-backed ingestion status per
registered source, kept separate from the static registry metadata in
app/registry/sources.py.

This split is deliberate: the registry (source_key, path, tier, strategy,
description, ...) is code -- it doesn't change at runtime and shouldn't live
in a database row. What DOES change at runtime is *what has actually
happened* to that source -- has its schema been validated, has data been
loaded, when, how many records. That belongs here.

Phase 1 only ever writes VALIDATED / VALIDATION_FAILED via
app/ingestion/validate_all.py's header/schema smoke checks -- never LOADED or
PARTIALLY_LOADED, since no full ingestion runs in this phase.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import IngestionStatus
from app.models.base import TimestampMixin


class DatasetSourceStatus(Base, TimestampMixin):
    __tablename__ = "dataset_source_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    status: Mapped[IngestionStatus] = mapped_column(
        SAEnum(IngestionStatus), nullable=False, default=IngestionStatus.NOT_INGESTED
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    record_count_ingested: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DatasetSourceStatus source_key={self.source_key!r} status={self.status}>"
