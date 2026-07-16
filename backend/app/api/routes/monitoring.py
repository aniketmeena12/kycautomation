"""
Monitoring API.

Synchronous by design: this project has no task queue (Celery/Redis are
explicitly out of scope -- docs/phase-0-dataset-audit.md SS11), and monitoring
is an operator-triggered action, not a request hot path. The `limit` cap on
`/monitor/all` exists so a sweep can't run unbounded inside one HTTP request;
a caller paginates with limit/offset.

A cycle NEVER 500s because a provider or one client failed -- failures come
back as data in the per-cycle result (`error`, `provider_failures`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.repositories.client_repository import ClientRepository
from app.risk.schemas import MonitoringCycleResult, MonitoringRunResult
from app.schemas.risk import MonitorAllRequest, MonitorClientRequest
from app.services.monitoring_service import MonitoringService

router = APIRouter(prefix="/monitor", tags=["monitoring"])


@router.post("/client/{client_id}", response_model=MonitoringCycleResult)
def monitor_client(
    client_id: int, request: MonitorClientRequest | None = None, db: Session = Depends(get_db)
) -> MonitoringCycleResult:
    """Run one monitoring cycle. `client_id` is the EXTERNAL client_id."""
    request = request or MonitorClientRequest()
    client = ClientRepository(db).get_by_external_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found. Has ingestion run?")

    return MonitoringService(db).monitor_client(
        client,
        include_providers=request.include_providers,
        include_resolution=request.include_resolution,
        allow_expensive_providers=request.allow_expensive_providers,
    )


@router.post("/all", response_model=MonitoringRunResult)
def monitor_all(
    request: MonitorAllRequest | None = None, db: Session = Depends(get_db)
) -> MonitoringRunResult:
    """Sweep a population: all (paginated), a selected list, or high-risk only."""
    request = request or MonitorAllRequest()
    service = MonitoringService(db)
    common = dict(
        include_providers=request.include_providers,
        include_resolution=request.include_resolution,
        allow_expensive_providers=request.allow_expensive_providers,
    )

    if request.external_client_ids:
        return service.monitor_selected(request.external_client_ids, **common)
    if request.high_risk_only:
        return service.monitor_high_risk(limit=request.limit, **common)
    return service.monitor_all(limit=request.limit, offset=request.offset, **common)
