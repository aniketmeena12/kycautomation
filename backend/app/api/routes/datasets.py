"""
Dataset ingestion status API -- distinct from /api/v1/sources (Phase 1's
full catalog + tier/strategy metadata). This endpoint is a focused
operational view: what's the current IngestionStatus of each source, right
now. Reuses the same registry + DatasetSourceStatus table as /api/v1/sources
-- no duplicated data model, just a different projection for a different
purpose (catalog browsing vs. operational status check).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_source_registry
from app.core.enums import IngestionStatus, SourceTier
from app.registry.sources import SourceRegistry
from app.repositories.dataset_status_repository import DatasetSourceStatusRepository

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DatasetStatusRead(BaseModel):
    source_key: str
    source_tier: SourceTier
    file_available: bool
    ingestion_status: IngestionStatus
    record_count_ingested: int | None = None


class DatasetStatusListResponse(BaseModel):
    datasets: list[DatasetStatusRead]
    loaded_count: int
    total: int


@router.get("/status", response_model=DatasetStatusListResponse)
def dataset_status(
    db: Session = Depends(get_db), registry: SourceRegistry = Depends(get_source_registry)
) -> DatasetStatusListResponse:
    status_repo = DatasetSourceStatusRepository(db)
    status_by_key = {row.source_key: row for row in status_repo.list_all()}

    datasets = []
    for source in registry.list_sources():
        status_row = status_by_key.get(source.source_key)
        datasets.append(
            DatasetStatusRead(
                source_key=source.source_key,
                source_tier=source.source_tier,
                file_available=registry.check_file_availability(source.source_key),
                ingestion_status=status_row.status if status_row else IngestionStatus.NOT_INGESTED,
                record_count_ingested=status_row.record_count_ingested if status_row else None,
            )
        )

    return DatasetStatusListResponse(
        datasets=datasets,
        loaded_count=sum(1 for d in datasets if d.ingestion_status == IngestionStatus.LOADED),
        total=len(datasets),
    )
