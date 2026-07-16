"""Persistence layer for Evidence. No Phase 2 code creates real Evidence rows
-- evidence requires investigative judgment (entity resolution, adverse-media
extraction), which is explicitly out of scope for this phase. This
repository exists so Customer360Service can read whatever evidence exists
(honestly empty today) without depending on a future phase's internals, and
so a future phase has a ready-made, tested write path."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.evidence import Evidence


class EvidenceRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, evidence_id: int) -> Evidence | None:
        return self._db.get(Evidence, evidence_id)

    def list_for_client(self, client_id: int) -> list[Evidence]:
        stmt = select(Evidence).where(Evidence.client_id == client_id).order_by(Evidence.created_at.desc())
        return list(self._db.scalars(stmt))

    def list_for_entity_match(self, entity_match_id: int) -> list[Evidence]:
        """The evidence-graph traversal: EntityMatch -> its Evidence rows.
        Many-to-one by design -- an entity can accumulate several independent
        pieces of evidence."""
        stmt = (
            select(Evidence)
            .where(Evidence.entity_match_id == entity_match_id)
            .order_by(Evidence.created_at.desc())
        )
        return list(self._db.scalars(stmt))

    def create(self, **fields) -> Evidence:
        evidence = Evidence(**fields)
        self._db.add(evidence)
        self._db.flush()
        return evidence
