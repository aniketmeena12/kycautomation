"""Evidence read API -- the evidence graph, exposed.

Read-only in Phase 3. Evidence is written by the services that produce it
(app/services/evidence_service.py), never by an API caller posting arbitrary
"facts" -- an endpoint that let a client invent evidence would undermine the
entire traceability story.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.repositories.client_repository import ClientRepository
from app.repositories.entity_match_repository import EntityMatchRepository
from app.schemas.resolution import EvidenceListResponse, EvidenceRead
from app.services.evidence_service import EvidenceService

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("/client/{client_id}", response_model=EvidenceListResponse)
def evidence_for_client(client_id: int, db: Session = Depends(get_db)) -> EvidenceListResponse:
    """All evidence linked to a client. `client_id` is the EXTERNAL id, matching
    the convention used by /api/v1/customers."""
    client = ClientRepository(db).get_by_external_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found. Has ingestion run?")
    rows = EvidenceService(db).list_for_client(client.id)
    return EvidenceListResponse(evidence=[EvidenceRead.model_validate(r) for r in rows], total=len(rows))


@router.get("/{entity_match_id}", response_model=EvidenceListResponse)
def evidence_for_entity_match(entity_match_id: int, db: Session = Depends(get_db)) -> EvidenceListResponse:
    """All evidence attached to one resolved entity match -- the
    EntityMatch -> Evidence edge of the graph. Multiple rows per match is
    normal."""
    if EntityMatchRepository(db).get_by_id(entity_match_id) is None:
        raise HTTPException(status_code=404, detail=f"EntityMatch {entity_match_id} not found.")
    rows = EvidenceService(db).list_for_entity_match(entity_match_id)
    return EvidenceListResponse(evidence=[EvidenceRead.model_validate(r) for r in rows], total=len(rows))
