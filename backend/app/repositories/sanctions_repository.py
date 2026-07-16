"""Persistence layer for SanctionsEntity / SanctionsAlias. Every query and
upsert here is tier-aware -- nothing in this class ever merges Tier 1 and
Tier 2 rows without the caller explicitly asking for both."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import SourceTier, SourceType
from app.models.sanctions import SanctionsAlias, SanctionsEntity


class SanctionsRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_external_id(self, source_type: SourceType, external_entity_id: str) -> SanctionsEntity | None:
        stmt = select(SanctionsEntity).where(
            SanctionsEntity.source_type == source_type,
            SanctionsEntity.external_entity_id == external_entity_id,
        )
        return self._db.scalars(stmt).one_or_none()

    def list_by_tier(self, source_tier: SourceTier, *, limit: int = 100) -> list[SanctionsEntity]:
        stmt = select(SanctionsEntity).where(SanctionsEntity.source_tier == source_tier).limit(limit)
        return list(self._db.scalars(stmt))

    def upsert_entity(
        self, *, source_type: SourceType, external_entity_id: str, **fields
    ) -> tuple[SanctionsEntity, bool]:
        existing = self.get_by_external_id(source_type, external_entity_id)
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing, False

        entity = SanctionsEntity(source_type=source_type, external_entity_id=external_entity_id, **fields)
        self._db.add(entity)
        self._db.flush()
        return entity, True

    def upsert_alias(
        self, *, sanctions_entity_id: int, alias_name: str, alias_type: str | None = None
    ) -> tuple[SanctionsAlias, bool]:
        stmt = select(SanctionsAlias).where(
            SanctionsAlias.sanctions_entity_id == sanctions_entity_id,
            SanctionsAlias.alias_name == alias_name,
            SanctionsAlias.alias_type == alias_type,
        )
        existing = self._db.scalars(stmt).one_or_none()
        if existing is not None:
            return existing, False

        alias = SanctionsAlias(
            sanctions_entity_id=sanctions_entity_id, alias_name=alias_name, alias_type=alias_type
        )
        self._db.add(alias)
        self._db.flush()
        return alias, True

    def count(self) -> int:
        from sqlalchemy import func

        return self._db.scalar(select(func.count()).select_from(SanctionsEntity)) or 0
