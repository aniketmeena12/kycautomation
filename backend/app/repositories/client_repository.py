"""Persistence layer for Client. Hides SQLAlchemy details from services --
Customer360Service and the ingestion loaders talk to this, never to
`db.query(Client)` directly."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.client import Client


class ClientRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, client_id: int) -> Client | None:
        return self._db.get(Client, client_id)

    def get_by_external_id(self, external_client_id: int) -> Client | None:
        stmt = select(Client).where(Client.external_client_id == external_client_id)
        return self._db.scalars(stmt).one_or_none()

    def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        sanctions_flag: bool | None = None,
        pep_flag: bool | None = None,
        sector_risk: str | None = None,
        mapped_only: bool = False,
    ) -> list[Client]:
        stmt = select(Client)
        if sanctions_flag is not None:
            stmt = stmt.where(Client.sanctions_flag == sanctions_flag)
        if pep_flag is not None:
            stmt = stmt.where(Client.pep_flag == pep_flag)
        if sector_risk is not None:
            stmt = stmt.where(Client.sector_risk == sector_risk)
        if mapped_only:
            stmt = stmt.where(Client.accounts.any())
        stmt = stmt.order_by(Client.external_client_id).offset(offset).limit(limit)
        return list(self._db.scalars(stmt))

    def count(self) -> int:
        return self._db.scalar(select(func.count()).select_from(Client)) or 0

    def map_external_to_internal_ids(self) -> dict[int, int]:
        """One query, used by loaders that need to resolve many external
        client_id values (e.g. a 50,000-row transaction file) without a
        per-row lookup."""
        stmt = select(Client.external_client_id, Client.id)
        return dict(self._db.execute(stmt).all())

    def upsert(self, *, external_client_id: int, **fields) -> tuple[Client, bool]:
        """Returns (client, created). Matches on external_client_id, the
        documented natural key (app/ingestion/base.py)."""
        existing = self.get_by_external_id(external_client_id)
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing, False

        client = Client(external_client_id=external_client_id, **fields)
        self._db.add(client)
        self._db.flush()
        return client, True
