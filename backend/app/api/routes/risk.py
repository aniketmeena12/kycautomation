"""Risk read API: current score, history, events, and the factor registry."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.repositories.client_repository import ClientRepository
from app.repositories.risk_repository import RiskEventRepository, RiskSnapshotRepository
from app.risk.config import get_risk_registry
from app.schemas.risk import (
    CurrentRiskResponse,
    RiskEventListResponse,
    RiskEventRead,
    RiskFactorListResponse,
    RiskFactorRead,
    RiskHistoryResponse,
    RiskScoreSnapshotRead,
)

router = APIRouter(tags=["risk"])


def _resolve_client(db: Session, external_client_id: int):
    client = ClientRepository(db).get_by_external_id(external_client_id)
    if client is None:
        raise HTTPException(
            status_code=404, detail=f"Client {external_client_id} not found. Has ingestion run?"
        )
    return client


@router.get("/risk/factors", response_model=RiskFactorListResponse)
def list_risk_factors() -> RiskFactorListResponse:
    """The live Risk Factor Registry.

    Declared before /risk/{client_id} so the literal path wins over the int
    converter -- FastAPI matches in declaration order.
    """
    registry = get_risk_registry()
    return RiskFactorListResponse(
        factors=[RiskFactorRead(**f.model_dump()) for f in registry.factors],
        total=len(registry.factors),
        enabled_count=len(registry.enabled_factors()),
        contribution_formula=registry.scoring.contribution_formula,
        scoring_logic_version=registry.scoring.scoring_logic_version,
        bands={b.value: threshold for b, threshold in registry.bands.items()},
    )


@router.get("/risk/history/{client_id}", response_model=RiskHistoryResponse)
def risk_history(
    client_id: int, db: Session = Depends(get_db), limit: int = Query(default=50, ge=1, le=500)
) -> RiskHistoryResponse:
    """Append-only score history, newest first."""
    client = _resolve_client(db, client_id)
    snapshots = RiskSnapshotRepository(db).history_for_client(client.id, limit=limit)
    return RiskHistoryResponse(
        client_id=client.id,
        external_client_id=client.external_client_id,
        snapshots=[RiskScoreSnapshotRead.model_validate(s) for s in snapshots],
        total=len(snapshots),
    )


@router.get("/risk/{client_id}", response_model=CurrentRiskResponse)
def current_risk(client_id: int, db: Session = Depends(get_db)) -> CurrentRiskResponse:
    client = _resolve_client(db, client_id)
    latest = RiskSnapshotRepository(db).latest_for_client(client.id)
    return CurrentRiskResponse(
        client_id=client.id,
        external_client_id=client.external_client_id,
        current=RiskScoreSnapshotRead.model_validate(latest) if latest else None,
        never_monitored=latest is None,
    )


@router.get("/events/{client_id}", response_model=RiskEventListResponse)
def client_events(
    client_id: int, db: Session = Depends(get_db), limit: int = Query(default=100, ge=1, le=500)
) -> RiskEventListResponse:
    client = _resolve_client(db, client_id)
    events = RiskEventRepository(db).list_for_client(client.id, limit=limit)
    return RiskEventListResponse(events=[RiskEventRead.model_validate(e) for e in events], total=len(events))
