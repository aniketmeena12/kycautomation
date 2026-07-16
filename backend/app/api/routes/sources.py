"""
Read-only dataset source status API.

Exposes registry metadata (source_key, category, tier, strategy, description)
merged with live file availability and the last-known DatasetSourceStatus
row. Never exposes a resolved absolute filesystem path -- only the
registry's relative_path, which is safe to show (it's checked-in project
structure, not a machine-specific path).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_source_registry
from app.core.enums import IngestionStatus
from app.models.source_status import DatasetSourceStatus
from app.registry.sources import SourceDefinition, SourceRegistry
from app.schemas.source import DatasetSourceListResponse, DatasetSourceRead

router = APIRouter(prefix="/sources", tags=["sources"])


def _to_schema(source: SourceDefinition, registry: SourceRegistry, db: Session) -> DatasetSourceRead:
    file_available = registry.check_file_availability(source.source_key)
    status_row = db.query(DatasetSourceStatus).filter_by(source_key=source.source_key).one_or_none()

    return DatasetSourceRead(
        source_key=source.source_key,
        display_name=source.display_name,
        relative_path=source.relative_path,
        category=source.category,
        source_tier=source.source_tier,
        source_type=source.source_type,
        format=source.format,
        known_record_count=source.known_record_count,
        enabled=source.enabled,
        ingestion_strategy=source.ingestion_strategy,
        description=source.description,
        file_available=file_available,
        ingestion_status=status_row.status if status_row else IngestionStatus.NOT_INGESTED,
        last_validated_at=status_row.last_validated_at if status_row else None,
        last_ingested_at=status_row.last_ingested_at if status_row else None,
    )


@router.get("", response_model=DatasetSourceListResponse)
def list_sources(
    db: Session = Depends(get_db),
    registry: SourceRegistry = Depends(get_source_registry),
) -> DatasetSourceListResponse:
    sources = [_to_schema(s, registry, db) for s in registry.list_sources()]
    return DatasetSourceListResponse(
        sources=sources,
        total=len(sources),
        available_count=sum(1 for s in sources if s.file_available),
    )


@router.get("/{source_key}", response_model=DatasetSourceRead)
def get_source(
    source_key: str,
    db: Session = Depends(get_db),
    registry: SourceRegistry = Depends(get_source_registry),
) -> DatasetSourceRead:
    source = registry.get_source(source_key)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Unknown source_key: {source_key}")
    return _to_schema(source, registry, db)
