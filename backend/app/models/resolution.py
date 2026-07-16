"""
EntityMatch -- a scored resolution between a subject (a Client, an
OwnershipEntity, or an adverse-media mention) and a candidate entity.

Phase 1 created this table as a contract and left it empty ("this table is a
contract for the future Entity Resolution Service"). Phase 3 populates it and
extends it additively -- no column was removed or repurposed.

Two Phase 3 changes worth understanding:

1. `candidate_sanctions_entity_id` is now NULLABLE. Phase 1 assumed every
   candidate is a row in our own `sanctions_entities` table. That is false for
   a provider-sourced candidate: the Tier-1 OFAC/OpenSanctions providers stream
   their files and never persist a row (docs/phase-2-ingestion.md SS3), so
   there is no local id to point at. Forcing one would mean bulk-loading those
   files purely to satisfy a foreign key -- exactly what ADR/Phase-2 forbids.
   Instead, a candidate is identified by whichever is available:
     - `candidate_sanctions_entity_id` (a real FK) when it came from our DB, or
     - `candidate_provider` + `candidate_external_id` when it came from a
       streaming provider.
   `candidate_name` is always stored so a match is human-readable without
   re-querying the source.

2. Scores and reasons are stored, not just the number. `matched_attributes`,
   `conflicting_attributes` and `reasons` are JSON-encoded lists so a
   persisted match can explain itself later, without re-running the pipeline
   (Phase 3 brief SS9/SS12).

`subject_type` + `subject_id` remains the documented lightweight polymorphic
association from Phase 1 (see EntityMatchSubjectType).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import EntityMatchStatus, EntityMatchSubjectType
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.sanctions import SanctionsEntity


class EntityMatch(Base, TimestampMixin):
    __tablename__ = "entity_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    subject_type: Mapped[EntityMatchSubjectType] = mapped_column(
        SAEnum(EntityMatchSubjectType), nullable=False
    )
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Human-readable, source-qualified ref (e.g. 'client:3'), mirroring
    # ResolutionSubject.subject_ref so a stored row ties back to a pipeline run.
    subject_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # Populated when the candidate is a row we actually hold. NULL for a
    # streaming-provider candidate -- see the module docstring.
    candidate_sanctions_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("sanctions_entities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    candidate: Mapped["SanctionsEntity | None"] = relationship()

    candidate_provider: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    candidate_external_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    candidate_name: Mapped[str] = mapped_column(String, nullable=False)
    candidate_source_tier: Mapped[str | None] = mapped_column(String, nullable=True)

    name_similarity_score: Mapped[float] = mapped_column(Float, nullable=False)
    corroboration_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    combined_confidence: Mapped[float] = mapped_column(Float, nullable=False, index=True)

    # JSON-encoded lists. See app/services/entity_resolution_service.py, which
    # is the only writer and owns the encoding.
    matched_attributes: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflicting_attributes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasons: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[EntityMatchStatus] = mapped_column(
        SAEnum(EntityMatchStatus), nullable=False, default=EntityMatchStatus.CANDIDATE, index=True
    )
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<EntityMatch id={self.id} subject={self.subject_type}:{self.subject_id} "
            f"candidate={self.candidate_name!r} conf={self.combined_confidence} status={self.status}>"
        )
