"""Alert read API.

Read-only in Phase 4. Acting on an alert (acknowledge / close / escalate) is
a human-review decision and belongs to a later phase -- exposing a mutation
endpoint now would let an automated caller resolve a compliance alert, which
is exactly what the project's core principle reserves for a person.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.enums import AlertStatus, RiskBand
from app.repositories.client_repository import ClientRepository
from app.repositories.risk_repository import AlertRepository
from app.schemas.risk import AlertDetailResponse, AlertListResponse, AlertRead, RiskEventRead

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=AlertListResponse)
def list_alerts(
    db: Session = Depends(get_db),
    client_id: int | None = Query(default=None, description="External client_id."),
    status: AlertStatus | None = None,
    severity: RiskBand | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AlertListResponse:
    internal_client_id = None
    if client_id is not None:
        client = ClientRepository(db).get_by_external_id(client_id)
        if client is None:
            raise HTTPException(status_code=404, detail=f"Client {client_id} not found.")
        internal_client_id = client.id

    alerts = AlertRepository(db).list(
        client_id=internal_client_id, status=status, severity=severity, limit=limit, offset=offset
    )
    return AlertListResponse(alerts=[AlertRead.model_validate(a) for a in alerts], total=len(alerts))


@router.get("/{alert_id}", response_model=AlertDetailResponse)
def get_alert(alert_id: int, db: Session = Depends(get_db)) -> AlertDetailResponse:
    alert = AlertRepository(db).get_by_id(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found.")
    return AlertDetailResponse(
        alert=AlertRead.model_validate(alert),
        risk_events=[RiskEventRead.model_validate(e) for e in alert.risk_events],
        evidence_ids=[e.id for e in alert.evidence],
    )
