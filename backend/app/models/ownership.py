"""
OwnershipEntity / OwnershipRelationship -- relational modeling of the two UBO
graph fixtures (data/ubo/simple_structure.json, showcase_structure.json).

Per docs/phase-0-dataset-audit.md SS11: no graph database is used. The two
fixture graphs have 3-4 nodes each; a self-referencing relational table with
an edge table fully supports the multi-hop traversal and effective-ownership
arithmetic (percentage multiplication across hops) that Phase 0's showcase
demo needs, without adding infrastructure the data doesn't justify.

`resolved_sanctions_entity_id` is nullable and unpopulated in Phase 1 -- it
exists as the contract point for the future Entity Resolution Service to
record a confirmed match (e.g. UBO-IND-004 -> the Tier-2 "AL-RASHID, Mohammad"
SanctionsEntity), not something Phase 1 computes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import ProvenanceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.sanctions import SanctionsEntity


class OwnershipEntity(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "ownership_entities"
    __table_args__ = (
        UniqueConstraint("graph_key", "external_entity_id", name="uq_ownership_entity_graph_external_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Which fixture graph this node belongs to, e.g. "simple_structure" or
    # "showcase_structure" -- the two graphs are independent, unconnected demo
    # cases and must never be traversed as one combined graph.
    graph_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    external_entity_id: Mapped[str] = mapped_column(String, nullable=False)  # e.g. "UBO-IND-004"

    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)  # "company" | "individual"
    nationality: Mapped[str | None] = mapped_column(String, nullable=True)
    dob: Mapped[str | None] = mapped_column(String, nullable=True)  # source stores year-only strings
    sector: Mapped[str | None] = mapped_column(String, nullable=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)

    resolved_sanctions_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("sanctions_entities.id"), nullable=True
    )
    resolved_sanctions_entity: Mapped["SanctionsEntity | None"] = relationship()

    owned_edges: Mapped[list["OwnershipRelationship"]] = relationship(
        back_populates="owner",
        foreign_keys="OwnershipRelationship.owner_id",
        cascade="all, delete-orphan",
    )
    owning_edges: Mapped[list["OwnershipRelationship"]] = relationship(
        back_populates="owned",
        foreign_keys="OwnershipRelationship.owned_id",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<OwnershipEntity id={self.id} graph_key={self.graph_key} name={self.name!r}>"


class OwnershipRelationship(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "ownership_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("ownership_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owned_id: Mapped[int] = mapped_column(
        ForeignKey("ownership_entities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    percentage: Mapped[float] = mapped_column(Float, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner: Mapped["OwnershipEntity"] = relationship(back_populates="owned_edges", foreign_keys=[owner_id])
    owned: Mapped["OwnershipEntity"] = relationship(back_populates="owning_edges", foreign_keys=[owned_id])

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<OwnershipRelationship owner_id={self.owner_id} owned_id={self.owned_id} pct={self.percentage}>"
        )
