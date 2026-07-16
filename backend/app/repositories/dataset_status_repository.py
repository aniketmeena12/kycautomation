"""Persistence layer for DatasetSourceStatus. Extracted from Phase 1's
app/ingestion/validate_all.py so both validation and Phase 2 ingestion write
status through the same code path."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core.enums import IngestionStatus
from app.models.source_status import DatasetSourceStatus


class DatasetSourceStatusRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, source_key: str) -> DatasetSourceStatus | None:
        return self._db.query(DatasetSourceStatus).filter_by(source_key=source_key).one_or_none()

    def list_all(self) -> list[DatasetSourceStatus]:
        return self._db.query(DatasetSourceStatus).all()

    def upsert(
        self,
        source_key: str,
        *,
        status: IngestionStatus,
        last_validated_at: datetime | None = None,
        last_ingested_at: datetime | None = None,
        record_count_ingested: int | None = None,
        notes: str | None = None,
    ) -> DatasetSourceStatus:
        row = self.get(source_key)
        if row is None:
            row = DatasetSourceStatus(source_key=source_key)
            self._db.add(row)

        row.status = status
        if last_validated_at is not None:
            row.last_validated_at = last_validated_at
        if last_ingested_at is not None:
            row.last_ingested_at = last_ingested_at
        # record_count_ingested and notes are set even when None, to allow
        # clearing a stale value on re-validation.
        row.record_count_ingested = record_count_ingested
        row.notes = notes

        self._db.commit()
        self._db.refresh(row)
        return row
