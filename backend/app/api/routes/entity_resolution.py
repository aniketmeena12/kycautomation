"""
Entity-resolution API.

`allow_expensive_providers` defaults to False on every route. The Tier-1
OpenSanctions provider streams 1.3M rows at ~40-45s per query
(docs/phase-2-ingestion.md SS3); making that the default would violate the
"never scan 1.3M records unless unavoidable" rule on every request. A caller
opts in explicitly and accepts the latency -- same posture as Customer 360's
opt-in lookups (ADR-009).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.enums import EntityMatchStatus, EntityMatchSubjectType
from app.models.ownership import OwnershipEntity
from app.repositories.client_repository import ClientRepository
from app.repositories.entity_match_repository import EntityMatchRepository
from app.resolution.pipeline import EntityResolutionPipeline
from app.resolution.schemas import EntityResolutionResult, ResolutionRunResult
from app.schemas.resolution import (
    EntityMatchRead,
    ResolveBatchRequest,
    ResolveBatchResponse,
    ResolvePairRequest,
    ResolveSubjectRequest,
)
from app.services.entity_resolution_service import EntityResolutionService

router = APIRouter(prefix="/entity-resolution", tags=["entity-resolution"])


def _run_one(db: Session, request: ResolveSubjectRequest) -> ResolutionRunResult:
    service = EntityResolutionService(db)

    if request.subject is not None:
        subject = request.subject
        subject_type = EntityMatchSubjectType.CLIENT
        subject_id = request.subject.internal_id or 0
        client_id = None
    elif request.client_id is not None:
        client = ClientRepository(db).get_by_external_id(request.client_id)
        if client is None:
            raise HTTPException(
                status_code=404, detail=f"Client {request.client_id} not found. Has ingestion run?"
            )
        subject = service.subject_for_client(client)
        subject_type = EntityMatchSubjectType.CLIENT
        subject_id = client.id
        client_id = client.id
    else:
        entity = db.get(OwnershipEntity, request.ownership_entity_id)
        if entity is None:
            raise HTTPException(
                status_code=404,
                detail=f"OwnershipEntity {request.ownership_entity_id} not found. Has ingestion run?",
            )
        subject = service.subject_for_ownership_entity(entity)
        subject_type = EntityMatchSubjectType.OWNERSHIP_ENTITY
        subject_id = entity.id
        client_id = None

    common = dict(
        include_local_db=request.include_local_db,
        include_providers=request.include_providers,
        allow_expensive_providers=request.allow_expensive_providers,
        source_tier=request.source_tier,
        min_confidence=request.min_confidence,
        max_results=request.max_results,
    )

    if not request.persist:
        # Pure scoring, nothing written -- the pipeline never persists on its own.
        return service.pipeline.resolve(db, subject, **common)

    return service.resolve_and_persist(
        subject, subject_type=subject_type, subject_id=subject_id, client_id=client_id, **common
    )


@router.post("/resolve", response_model=ResolutionRunResult)
def resolve(request: ResolveSubjectRequest, db: Session = Depends(get_db)) -> ResolutionRunResult:
    return _run_one(db, request)


@router.post("/resolve-pair", response_model=EntityResolutionResult)
def resolve_pair(request: ResolvePairRequest) -> EntityResolutionResult:
    """Score two supplied entities against each other. No database, no
    providers, nothing persisted -- pure, deterministic, and the clearest
    demonstration that the engine works for entities it has never seen."""
    return EntityResolutionPipeline().resolve_pair(request.subject, request.candidate)


@router.post("/batch", response_model=ResolveBatchResponse)
def resolve_batch(request: ResolveBatchRequest, db: Session = Depends(get_db)) -> ResolveBatchResponse:
    """Sequential, not concurrent, and deliberately capped at 50 subjects.

    Each subject can trigger provider I/O; fanning those out concurrently
    against a single SQLite session and shared streaming providers would risk
    the exact problems Phase 1/2 designed against (single-writer SQLite --
    ADR-001). Batch here means "one request, many subjects", not "parallel".
    """
    runs = [_run_one(db, subject_request) for subject_request in request.subjects]
    return ResolveBatchResponse(
        runs=runs,
        total_subjects=len(runs),
        total_results=sum(len(r.results) for r in runs),
    )


@router.get("/matches", response_model=list[EntityMatchRead])
def list_matches(
    db: Session = Depends(get_db),
    subject_ref: str | None = Query(
        default=None, description="e.g. 'client:3' or 'ownership:showcase_structure:UBO-IND-004'"
    ),
    status: EntityMatchStatus | None = None,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[EntityMatchRead]:
    repo = EntityMatchRepository(db)
    if subject_ref:
        matches = repo.list_for_subject(subject_ref, limit=limit)
    elif status:
        matches = repo.list_by_status(status, limit=limit)
    else:
        raise HTTPException(status_code=400, detail="Provide either subject_ref or status.")
    return [EntityMatchRead.model_validate(m) for m in matches]


@router.get("/{match_id}", response_model=EntityMatchRead)
def get_match(match_id: int, db: Session = Depends(get_db)) -> EntityMatchRead:
    match = EntityMatchRepository(db).get_by_id(match_id)
    if match is None:
        raise HTTPException(status_code=404, detail=f"EntityMatch {match_id} not found.")
    return EntityMatchRead.model_validate(match)
