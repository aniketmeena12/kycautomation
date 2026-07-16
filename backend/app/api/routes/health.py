"""
Health endpoints.

/health/live: pure liveness -- if this doesn't return 200, the process itself
is broken. No dependencies checked.

/health/ready: checks the things the app actually needs to serve traffic --
database connectivity and dataset registry availability. Deliberately does
NOT require every registered dataset file to be present, and never claims a
large source has been INGESTED just because it's reachable on disk -- see
DatasetSourceStatus vs. file_available in the /sources response, and
docs/phase-1-foundation.md's "SOURCE AVAILABLE vs SOURCE INGESTED" section.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_source_registry
from app.registry.sources import SourceRegistry
from app.schemas.health import ComponentCheck, LivenessResponse, ReadinessResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live", response_model=LivenessResponse)
def liveness() -> LivenessResponse:
    return LivenessResponse(status="alive")


@router.get("/ready", response_model=ReadinessResponse)
def readiness(
    db: Session = Depends(get_db),
    registry: SourceRegistry = Depends(get_source_registry),
) -> ReadinessResponse:
    checks: list[ComponentCheck] = []
    overall_ok = True

    try:
        db.execute(text("SELECT 1"))
        checks.append(ComponentCheck(name="database", status="ok"))
    except Exception as exc:
        overall_ok = False
        checks.append(ComponentCheck(name="database", status="error", detail=str(exc)))

    try:
        sources = registry.list_sources()
        available = sum(1 for s in sources if registry.check_file_availability(s.source_key))
        checks.append(
            ComponentCheck(
                name="dataset_registry",
                status="ok",
                detail=f"{available}/{len(sources)} registered sources available on disk "
                "(availability != ingestion -- see /api/v1/sources).",
            )
        )
    except Exception as exc:
        overall_ok = False
        checks.append(ComponentCheck(name="dataset_registry", status="error", detail=str(exc)))

    return ReadinessResponse(status="ready" if overall_ok else "not_ready", checks=checks)
