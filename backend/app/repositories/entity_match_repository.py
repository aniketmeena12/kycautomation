"""Persistence layer for EntityMatch.

Idempotency key: (subject_ref, candidate_provider, candidate_external_id).
Re-resolving the same pair updates the existing row rather than accumulating
a new one per run -- consistent with the natural-key upsert convention
established in Phase 2 (app/ingestion/base.py), so a re-run is always safe.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import EntityMatchStatus
from app.models.resolution import EntityMatch


class EntityMatchRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, match_id: int) -> EntityMatch | None:
        return self._db.get(EntityMatch, match_id)

    def find(
        self, *, subject_ref: str, candidate_provider: str | None, candidate_external_id: str | None
    ) -> EntityMatch | None:
        stmt = select(EntityMatch).where(
            EntityMatch.subject_ref == subject_ref,
            EntityMatch.candidate_provider == candidate_provider,
            EntityMatch.candidate_external_id == candidate_external_id,
        )
        return self._db.scalars(stmt).first()

    def list_for_subject(self, subject_ref: str, *, limit: int = 100) -> list[EntityMatch]:
        stmt = (
            select(EntityMatch)
            .where(EntityMatch.subject_ref == subject_ref)
            .order_by(EntityMatch.combined_confidence.desc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt))

    def list_by_status(self, status: EntityMatchStatus, *, limit: int = 100) -> list[EntityMatch]:
        stmt = (
            select(EntityMatch)
            .where(EntityMatch.status == status)
            .order_by(EntityMatch.combined_confidence.desc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt))

    def upsert(
        self, *, subject_ref: str, candidate_provider: str | None, candidate_external_id: str | None, **fields
    ) -> tuple[EntityMatch, bool]:
        existing = self.find(
            subject_ref=subject_ref,
            candidate_provider=candidate_provider,
            candidate_external_id=candidate_external_id,
        )
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing, False

        match = EntityMatch(
            subject_ref=subject_ref,
            candidate_provider=candidate_provider,
            candidate_external_id=candidate_external_id,
            **fields,
        )
        self._db.add(match)
        self._db.flush()
        return match, True
