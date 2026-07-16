"""
Ingestion API. `source_key` is always validated against the registry --
never used to construct or accept an arbitrary filesystem path (Security
Baseline, docs/phase-1-foundation.md).

POST /api/v1/ingestion/load can take up to ~45s for the full small-dataset
pipeline (dominated by the 50,000-row shallow transaction file -- see
docs/phase-2-ingestion.md SS3 for the measured breakdown). This is
synchronous by design: no task queue exists in this project (Celery/Redis
are explicitly out of scope, docs/phase-0-dataset-audit.md SS11), and
ingestion is an infrequent, operator-triggered action, not a request-path
hot path.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.ingestion.commands import ingest_all, ingest_dataset, validate_sources
from app.schemas.ingestion import IngestionLoadRequest, IngestionResultsResponse, IngestionValidateRequest

router = APIRouter(prefix="/ingestion", tags=["ingestion"])


@router.post("/validate", response_model=IngestionResultsResponse)
def validate(request: IngestionValidateRequest, db: Session = Depends(get_db)) -> IngestionResultsResponse:
    results = validate_sources(db, source_keys=request.source_keys)
    return IngestionResultsResponse(results=results)


@router.post("/load", response_model=IngestionResultsResponse)
def load(request: IngestionLoadRequest, db: Session = Depends(get_db)) -> IngestionResultsResponse:
    if request.all:
        results = ingest_all(db, include_large=request.include_large)
        return IngestionResultsResponse(results=results)

    try:
        result = ingest_dataset(db, request.source_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return IngestionResultsResponse(results=[result])
