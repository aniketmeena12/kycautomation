"""
Investigation API (Phase 5 brief SS11).

    POST /investigations/run/{client_id}   run an investigation
    POST /investigations/{id}/rerun        re-investigate; creates a NEW row
    GET  /investigations/{id}              one investigation, with grounding
    GET  /investigations/client/{id}       a client's investigation history
    GET  /investigations/agent/status      is the agent configured, and with what

READ-ONLY BEYOND RUNNING
-------------------------
There is deliberately no endpoint to close an investigation, accept a
recommendation, or change a status. Acting on an investigation is a human
compliance decision and belongs to a later, human-review phase -- the same
boundary Phase 4 drew around alerts, which are equally read-only. Exposing a
close endpoint here would let an API client (or a script) finish a compliance
decision no human ever made.

A FAILED RUN IS 200, NOT 5xx
-----------------------------
An unconfigured or unavailable provider produces an Investigation row with
status FAILED and the reason, returned with 200. This is not swallowing an
error: the run genuinely happened, produced a durable record, and its outcome
was "could not investigate" -- which is a result a caller must be able to read,
not an exception. 5xx is reserved for this service being broken, not for the
model being unreachable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.providers.llm_registry import UnknownLLMProviderError
from app.schemas.investigation import (
    AgentStatusResponse,
    InvestigationDetailResponse,
    InvestigationListResponse,
    RunInvestigationRequest,
    build_detail_response,
    investigation_read,
)
from app.services.investigation_service import (
    ClientNotFoundError,
    InvestigationNotFoundError,
    InvestigationOrchestrator,
)

router = APIRouter(tags=["investigations"])


def _orchestrator(db: Session) -> InvestigationOrchestrator:
    try:
        return InvestigationOrchestrator(db)
    except UnknownLLMProviderError as exc:
        # Misconfiguration of THIS service, not a model problem -- 500 is right.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/investigations/agent/status", response_model=AgentStatusResponse)
def agent_status(db: Session = Depends(get_db)) -> AgentStatusResponse:
    """Which provider/model is wired up, and whether it could actually run.

    Declared before /investigations/{id} so the literal path wins over the int
    converter -- FastAPI matches in declaration order (same reason
    /risk/factors precedes /risk/{client_id}).
    """
    return AgentStatusResponse(**_orchestrator(db).agent_status())


@router.post("/investigations/run/{external_client_id}", response_model=InvestigationDetailResponse)
def run_investigation(
    external_client_id: int,
    request: RunInvestigationRequest | None = None,
    db: Session = Depends(get_db),
) -> InvestigationDetailResponse:
    orchestrator = _orchestrator(db)
    payload = request or RunInvestigationRequest()

    try:
        if payload.alert_id is not None:
            investigation = orchestrator.run_for_alert(payload.alert_id)
        else:
            investigation = orchestrator.run_for_client(
                external_client_id, trigger_reason=payload.trigger_reason
            )
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvestigationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return build_detail_response(investigation)


@router.post("/investigations/{investigation_id}/rerun", response_model=InvestigationDetailResponse)
def rerun_investigation(investigation_id: int, db: Session = Depends(get_db)) -> InvestigationDetailResponse:
    """Re-investigate the same client. Creates a NEW investigation; the
    original is never modified. Compare `evaluation.context_hash` across the
    two to tell model variance from genuinely new evidence."""
    try:
        investigation = _orchestrator(db).rerun(investigation_id)
    except (InvestigationNotFoundError, ClientNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return build_detail_response(investigation)


@router.get("/investigations/client/{external_client_id}", response_model=InvestigationListResponse)
def list_client_investigations(
    external_client_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> InvestigationListResponse:
    try:
        client, investigations, total = _orchestrator(db).list_for_client(
            external_client_id, limit=limit, offset=offset
        )
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return InvestigationListResponse(
        client_id=client.id,
        external_client_id=client.external_client_id,
        investigations=[investigation_read(i) for i in investigations],
        total=total,
    )


@router.get("/investigations/{investigation_id}", response_model=InvestigationDetailResponse)
def get_investigation(investigation_id: int, db: Session = Depends(get_db)) -> InvestigationDetailResponse:
    try:
        investigation = _orchestrator(db).get(investigation_id)
    except InvestigationNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return build_detail_response(investigation)
