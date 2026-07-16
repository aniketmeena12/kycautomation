"""
SanctionsEntity / SanctionsAlias -- covers OFAC SDN and OpenSanctions, both
Tier 1 (authoritative, full-scale) and Tier 2 (curated demo fixture).

This is the concrete implementation of the provenance requirement from the
Phase 1 brief: source_tier and source_type are mandatory, non-nullable
columns on every row. There is no code path that can insert a sanctions
record without declaring which tier it came from, and nothing in this schema
merges Tier 1 and Tier 2 rows into a single unlabelled pool -- a query can
always filter by source_tier before treating a match as authoritative.

`entity_type` is deliberately a free-text string, not an enum: OFAC's
SDN_Type vocabulary (individual/vessel/aircraft/blank-meaning-entity) and
OpenSanctions' `schema` vocabulary (Person/Company/LegalEntity/Security/
CryptoWallet/Organization/Vessel/Airplane/Address/PublicBody) are not
compatible, and forcing them into one enum would lose information Phase 2's
entity-resolution corroboration logic needs (see docs/data-dictionary.md).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import Date, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import ProvenanceMixin, TimestampMixin

if TYPE_CHECKING:
    pass


class SanctionsEntity(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "sanctions_entities"
    __table_args__ = (
        UniqueConstraint("source_type", "external_entity_id", name="uq_sanctions_entity_source_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # e.g. OFAC's ent_num ("001923") or OpenSanctions' id ("NK-223CQDBzp8...").
    # Unique only in combination with source_type -- OFAC and OpenSanctions
    # ID spaces are independent and may collide as raw strings.
    external_entity_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    program_or_dataset: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Free-text remarks straight from the source file. Per the security
    # baseline (docs/phase-1-foundation.md SS"Security Baseline"): this is
    # DATA, never executed or treated as instructions by any future agent.
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)

    aliases: Mapped[list["SanctionsAlias"]] = relationship(
        back_populates="entity", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SanctionsEntity id={self.id} tier={self.source_tier} name={self.name!r}>"


class SanctionsAlias(Base, TimestampMixin):
    __tablename__ = "sanctions_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sanctions_entity_id: Mapped[int] = mapped_column(
        ForeignKey("sanctions_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    alias_type: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. aka / fka / nka

    entity: Mapped["SanctionsEntity"] = relationship(back_populates="aliases")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SanctionsAlias id={self.id} alias_name={self.alias_name!r}>"
