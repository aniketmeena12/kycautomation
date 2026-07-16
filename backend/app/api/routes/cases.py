"""
Case-management API (Phase 6 brief SS9).

    GET  /cases                  the queue
    GET  /cases/metrics          brief SS8
    POST /cases                  open a case
    GET  /cases/{id}             the workspace
    GET  /cases/{id}/timeline    generated chronology
    POST /cases/{id}/review      a human decision
    GET  /cases/{id}/audit       immutable trail
    POST /cases/{id}/sar         generate a Draft SAR
    GET  /cases/{id}/sar         read the Draft SAR

AN ILLEGAL TRANSITION IS 409, NOT 400
--------------------------------------
409 Conflict is the honest code: the request is well-formed and the action
would be valid at another time -- it conflicts with the case's CURRENT state.
400 would tell a caller their payload is malformed, sending them to debug a
correct request. The response body names the actions that ARE permitted now.

NOTHING HERE DECIDES
--------------------
There is no endpoint that closes a case, approves a SAR, or confirms a match on
its own authority. Each of those exists only as a reviewer ACTION on
POST /cases/{id}/review, and every one requires a named `reviewer`. The API
surface has no path by which a script could complete a compliance decision that
no human made.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.casework.schemas import CaseMetrics
from app.core.enums import CaseStatus
from app.schemas.case import (
    AuditEntryRead,
    CaseAuditResponse,
    CaseDetailResponse,
    CaseListResponse,
    CaseTimelineResponse,
    GenerateSARRequest,
    OpenCaseRequest,
    ReviewRequest,
    SARDraftRead,
    review_read,
    sar_read,
)
from app.services.case_service import (
    CaseNotFoundError,
    CaseService,
    ClientNotFoundError,
    ReviewRejectedError,
)
from app.services.customer360_service import Customer360Service

router = APIRouter(tags=["cases"])


def _service(db: Session) -> CaseService:
    return CaseService(db)


@router.get("/cases/metrics", response_model=CaseMetrics)
def case_metrics(db: Session = Depends(get_db)) -> CaseMetrics:
    """Declared before /cases/{case_id} so the literal path wins over the int
    converter -- FastAPI matches in declaration order (the same hazard that
    puts /risk/factors ahead of /risk/{client_id})."""
    return _service(db).metrics()


@router.get("/cases", response_model=CaseListResponse)
def list_cases(
    status: CaseStatus | None = None,
    assigned_to: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> CaseListResponse:
    cases, total = _service(db).list_cases(status=status, assigned_to=assigned_to, limit=limit, offset=offset)
    return CaseListResponse(cases=cases, total=total)


@router.post("/cases", response_model=CaseDetailResponse, status_code=201)
def open_case(request: OpenCaseRequest, db: Session = Depends(get_db)) -> CaseDetailResponse:
    service = _service(db)
    try:
        case = service.open_case_for_client(
            request.external_client_id,
            title=request.title,
            reason=request.reason,
            alert_id=request.alert_id,
            investigation_id=request.investigation_id,
            assigned_to=request.assigned_to,
        )
    except ClientNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return _detail(service, db, case.id)


@router.get("/cases/{case_id}", response_model=CaseDetailResponse)
def get_case(case_id: int, db: Session = Depends(get_db)) -> CaseDetailResponse:
    return _detail(_service(db), db, case_id)


@router.get("/cases/{case_id}/timeline", response_model=CaseTimelineResponse)
def get_timeline(case_id: int, db: Session = Depends(get_db)) -> CaseTimelineResponse:
    try:
        return CaseTimelineResponse(timeline=_service(db).timeline(case_id))
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/cases/{case_id}/review", response_model=CaseDetailResponse)
def submit_review(case_id: int, request: ReviewRequest, db: Session = Depends(get_db)) -> CaseDetailResponse:
    service = _service(db)
    try:
        service.apply_review(
            case_id,
            reviewer=request.reviewer,
            action=request.action,
            comment=request.comment,
            target_type=request.target_type,
            target_id=request.target_id,
        )
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewRejectedError as exc:
        # 409: the request is well-formed but conflicts with the current state.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _detail(service, db, case_id)


@router.get("/cases/{case_id}/audit", response_model=CaseAuditResponse)
def get_audit(
    case_id: int,
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> CaseAuditResponse:
    try:
        entries = _service(db).audit_trail(case_id, limit=limit)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CaseAuditResponse(
        case_id=case_id,
        entries=[AuditEntryRead.model_validate(e) for e in entries],
        total=len(entries),
    )


@router.post("/cases/{case_id}/sar", response_model=SARDraftRead, status_code=201)
def generate_sar(case_id: int, request: GenerateSARRequest, db: Session = Depends(get_db)) -> SARDraftRead:
    """Generate a Draft SAR and move the case to SAR_REVIEW.

    Always a DRAFT. There is no endpoint that files one, and approving it is a
    separate human action on /review.
    """
    try:
        sar = _service(db).generate_sar(case_id, requested_by=request.requested_by)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ReviewRejectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return sar_read(sar)


@router.get("/cases/{case_id}/sar", response_model=SARDraftRead)
def get_sar(case_id: int, db: Session = Depends(get_db)) -> SARDraftRead:
    try:
        sar = _service(db).latest_sar(case_id)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if sar is None:
        raise HTTPException(
            status_code=404,
            detail=f"No SAR draft exists for case {case_id}. POST /cases/{case_id}/sar to generate one.",
        )
    return sar_read(sar)


# --------------------------------------------------------------------- #


def _detail(service: CaseService, db: Session, case_id: int) -> CaseDetailResponse:
    """Aggregate the workspace from live rows (brief SS2).

    Customer 360 is called with live lookups OFF (ADR-009): opening a case must
    not fire provider queries, and the workspace must show what the risk score
    was computed from, not a fresher picture that would silently disagree with it.
    """
    try:
        case = service.get(case_id)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    summary = service._summarize(case)
    customer = Customer360Service(db).get_customer_360(case.client_id)

    from app.repositories.entity_match_repository import EntityMatchRepository
    from app.repositories.investigation_repository import InvestigationRepository
    from app.repositories.risk_repository import AlertRepository, RiskEventRepository, RiskSnapshotRepository

    snapshots = RiskSnapshotRepository(db).history_for_client(case.client_id, limit=50)
    events = RiskEventRepository(db).list_for_client(case.client_id, limit=100)
    alerts = AlertRepository(db).list(client_id=case.client_id, limit=100)
    matches = EntityMatchRepository(db).list_for_subject(f"client:{case.client.external_client_id}", limit=50)
    investigations = InvestigationRepository(db).list_for_client(case.client_id, limit=50)

    return CaseDetailResponse(
        case=summary,
        available_actions=service.available_actions(case),
        action_requirements=service.action_requirements(case),
        customer=customer.model_dump(mode="json"),
        risk_current=(
            {
                "score": snapshots[0].current_score,
                "band": snapshots[0].risk_band.value,
                "computed_at": snapshots[0].computed_at.isoformat(),
                "explanation": snapshots[0].trigger_reason,
            }
            if snapshots
            else None
        ),
        risk_history=[
            {
                "id": s.id,
                "score": s.current_score,
                "band": s.risk_band.value,
                "delta": s.delta,
                "computed_at": s.computed_at.isoformat(),
            }
            for s in snapshots
        ],
        risk_events=[
            {
                "id": e.id,
                "type": e.event_type.value,
                "severity": e.severity.value,
                "summary": e.summary,
                "detected_at": e.detected_at.isoformat(),
                "factor_id": e.factor_id,
            }
            for e in events
        ],
        entity_matches=[
            {
                "id": m.id,
                "candidate_name": m.candidate_name,
                "status": m.status.value,
                "confidence": m.combined_confidence,
                "source_tier": m.candidate_source_tier,
            }
            for m in matches
        ],
        evidence=[e.model_dump(mode="json") for e in customer.evidence],
        alerts=[
            {
                "id": a.id,
                "trigger": a.trigger.value,
                "severity": a.severity.value,
                "status": a.status.value,
                "reason": a.reason,
                "opened_at": a.opened_at.isoformat(),
            }
            for a in alerts
        ],
        investigations=[
            {
                "id": i.id,
                "status": i.status.value,
                "summary": i.summary,
                "grounding_passed": i.grounding_passed,
                "llm_model": i.llm_model,
                "opened_at": i.opened_at.isoformat(),
                "error_message": i.error_message,
            }
            for i in investigations
        ],
        reviews=[review_read(r) for r in case.reviews],
        sar_drafts=[sar_read(s, include_content=False) for s in case.sar_drafts],
        human_decision_required=case.status != CaseStatus.CLOSED,
    )
