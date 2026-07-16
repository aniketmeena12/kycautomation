"""
DatasetLoader -- the base class for every real (non-large, non-lookup-only)
ingestion loader.

Every loader:
  - Reads its own registered SourceDefinition (never a hard-coded path).
  - Upserts through a repository (app/repositories/), never touches
    SQLAlchemy models directly, so identity/uniqueness logic lives in
    exactly one place.
  - Returns an IngestionResult with real counts -- created vs. updated is
    tracked explicitly so re-running a loader is visibly idempotent, not
    just "didn't crash."
  - Flags an in-file duplicate natural key as an IngestionError (informational
    -- the upsert still makes it safe, but a duplicate key within one file is
    worth surfacing).
  - NEVER loads a source registered as LOOKUP_ONLY -- see
    app/ingestion/loaders/registry.py, which only maps loaders for
    FULL_LOAD/CURATED_FIXTURE sources.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.ingestion.results import IngestionError, IngestionResult, IngestionResultStatus
from app.registry.sources import SourceDefinition, SourceRegistry


class DatasetLoader(ABC):
    source_key: str

    def __init__(self, registry: SourceRegistry | None = None) -> None:
        self._registry = registry or SourceRegistry()

    @abstractmethod
    def load(self, db: Session) -> IngestionResult: ...

    def source(self) -> SourceDefinition:
        source = self._registry.get_source(self.source_key)
        if source is None:
            raise ValueError(f"No registered source for loader key '{self.source_key}'")
        return source

    def path(self) -> Path:
        return self._registry.resolve_path(self.source())

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _not_found_result(self, started_at: datetime) -> IngestionResult:
        return IngestionResult(
            source_key=self.source_key,
            status=IngestionResultStatus.SKIPPED_NOT_FOUND,
            started_at=started_at,
            completed_at=self._now(),
            notes=f"File not found for source '{self.source_key}' at {self.path()}.",
        )

    @staticmethod
    def _duplicate_key_error(row_number: int, key_value: object) -> IngestionError:
        return IngestionError(
            row_number=row_number,
            field="natural_key",
            message=f"Duplicate natural key '{key_value}' within source file (upserted, not duplicated).",
        )
