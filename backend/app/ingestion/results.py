"""Ingestion result/error contracts. Every validator and (in future phases)
every real ingestion job returns an IngestionResult -- never a bare
True/False or a raised exception for an expected failure mode (a missing
file, a header mismatch)."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class IngestionResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED_NOT_FOUND = "SKIPPED_NOT_FOUND"
    # Phase 2 additions:
    SKIPPED_LOOKUP_ONLY = "SKIPPED_LOOKUP_ONLY"  # source is served by a lazy provider, never bulk-loaded
    SKIPPED_AUXILIARY = "SKIPPED_AUXILIARY"  # ingested as a side effect of another source's loader


class IngestionError(BaseModel):
    row_number: int | None = None
    field: str | None = None
    message: str
    raw_value: str | None = None


class IngestionResult(BaseModel):
    source_key: str
    status: IngestionResultStatus
    started_at: datetime
    completed_at: datetime
    records_read: int = 0
    records_valid: int = 0
    records_invalid: int = 0
    errors: list[IngestionError] = Field(default_factory=list)
    notes: str | None = None
