"""Persistence layer for OwnershipEntity / OwnershipRelationship.

Deliberately scoped per graph_key -- the two UBO fixture graphs
('simple_structure', 'showcase_structure') are independent and must never be
traversed as one combined graph (docs/phase-0-dataset-audit.md SS4.7). There
is no method here that returns entities across multiple graphs at once."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ownership import OwnershipEntity, OwnershipRelationship


class OwnershipRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_entity(self, graph_key: str, external_entity_id: str) -> OwnershipEntity | None:
        stmt = select(OwnershipEntity).where(
            OwnershipEntity.graph_key == graph_key,
            OwnershipEntity.external_entity_id == external_entity_id,
        )
        return self._db.scalars(stmt).one_or_none()

    def get_graph(self, graph_key: str) -> tuple[list[OwnershipEntity], list[OwnershipRelationship]]:
        entities = list(
            self._db.scalars(select(OwnershipEntity).where(OwnershipEntity.graph_key == graph_key))
        )
        entity_ids = {e.id for e in entities}
        if not entity_ids:
            return [], []
        edges = list(
            self._db.scalars(
                select(OwnershipRelationship).where(OwnershipRelationship.owner_id.in_(entity_ids))
            )
        )
        return entities, edges

    def list_graph_keys(self) -> list[str]:
        stmt = select(OwnershipEntity.graph_key).distinct()
        return list(self._db.scalars(stmt))

    def upsert_entity(
        self, *, graph_key: str, external_entity_id: str, **fields
    ) -> tuple[OwnershipEntity, bool]:
        existing = self.get_entity(graph_key, external_entity_id)
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing, False

        entity = OwnershipEntity(graph_key=graph_key, external_entity_id=external_entity_id, **fields)
        self._db.add(entity)
        self._db.flush()
        return entity, True

    def upsert_relationship(
        self, *, owner_id: int, owned_id: int, source_dataset: str, **fields
    ) -> tuple[OwnershipRelationship, bool]:
        stmt = select(OwnershipRelationship).where(
            OwnershipRelationship.owner_id == owner_id,
            OwnershipRelationship.owned_id == owned_id,
            OwnershipRelationship.source_dataset == source_dataset,
        )
        existing = self._db.scalars(stmt).one_or_none()
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing, False

        edge = OwnershipRelationship(
            owner_id=owner_id, owned_id=owned_id, source_dataset=source_dataset, **fields
        )
        self._db.add(edge)
        self._db.flush()
        return edge, True
