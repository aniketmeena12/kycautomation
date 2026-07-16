"""Dataset source registry API contracts. Deliberately excludes anything
machine-specific: `relative_path` is relative to the configured raw data
directory, never a resolved absolute filesystem path (see app/api/routes/
sources.py)."""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import (
    IngestionStatus,
    IngestionStrategy,
    SourceCategory,
    SourceFormat,
    SourceTier,
    SourceType,
)


class DatasetSourceRead(BaseModel):
    source_key: str
    display_name: str
    relative_path: str
    category: SourceCategory
    source_tier: SourceTier
    source_type: SourceType
    format: SourceFormat
    known_record_count: int | None
    enabled: bool
    ingestion_strategy: IngestionStrategy
    description: str

    file_available: bool
    ingestion_status: IngestionStatus
    last_validated_at: datetime | None
    last_ingested_at: datetime | None


class DatasetSourceListResponse(BaseModel):
    sources: list[DatasetSourceRead]
    total: int
    available_count: int
