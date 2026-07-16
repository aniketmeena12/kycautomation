"""
Customer (Client) API.

`{client_id}` in every route below is the EXTERNAL client_id from
clients_with_fatf_ofac.csv (the identifier a caller actually knows), not the
internal surrogate primary key -- see app/models/client.py's provenance
design. This is deliberate: an API consumer thinks in terms of the source
dataset's client_id, and the internal/external ID split exists precisely so
that distinction can be made without leaking implementation detail into the
URL.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_customer360_service, get_db
from app.repositories.client_repository import ClientRepository
from app.schemas.client import ClientRead
from app.schemas.customer360 import Customer360Response
from app.services.customer360_service import ClientNotFoundError, Customer360Service

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[ClientRead])
def list_customers(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sanctions_flag: bool | None = None,
    pep_flag: bool | None = None,
    sector_risk: str | None = None,
    mapped_only: bool = False,
) -> list[ClientRead]:
    repo = ClientRepository(db)
    clients = repo.list(
        limit=limit,
        offset=offset,
        sanctions_flag=sanctions_flag,
        pep_flag=pep_flag,
        sector_risk=sector_risk,
        mapped_only=mapped_only,
    )
    return [ClientRead.model_validate(c) for c in clients]


@router.get("/{client_id}", response_model=ClientRead)
def get_customer(client_id: int, db: Session = Depends(get_db)) -> ClientRead:
    client = ClientRepository(db).get_by_external_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found. Has ingestion run?")
    return ClientRead.model_validate(client)


@router.get("/{client_id}/360", response_model=Customer360Response)
def get_customer_360(
    client_id: int,
    service: Customer360Service = Depends(get_customer360_service),
    db: Session = Depends(get_db),
    include_sanctions_lookup: bool = Query(
        False,
        description="Query all registered sanctions providers (can take up to ~60s -- see docs/phase-2-ingestion.md).",
    ),
    include_adverse_media_lookup: bool = Query(
        False, description="Query all registered adverse-media providers."
    ),
    include_deep_transactions: bool = Query(
        False, description="Stream SAML-D for this client's mapped accounts (can take up to ~45s)."
    ),
) -> Customer360Response:
    client = ClientRepository(db).get_by_external_id(client_id)
    if client is None:
        raise HTTPException(status_code=404, detail=f"Client {client_id} not found. Has ingestion run?")

    try:
        return service.get_customer_360(
            client.id,
            include_sanctions_lookup=include_sanctions_lookup,
            include_adverse_media_lookup=include_adverse_media_lookup,
            include_deep_transactions=include_deep_transactions,
        )
    except ClientNotFoundError as exc:  # pragma: no cover -- guarded above, kept for defense in depth
        raise HTTPException(status_code=404, detail=str(exc)) from exc
