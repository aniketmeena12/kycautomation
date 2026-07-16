"""
Evidence -- the central, first-class provenance-bearing fact record.

Every risk-relevant fact the system produces -- whether from a Phase 0 file,
a local reference dataset, or (in a future phase) a live external API -- is
stored here as one Evidence row, never as a bare log line or unstructured
text dump. This is the concrete mechanism behind two requirements:

  1. "Every risk decision must be traceable to evidence" (project rules).
  2. "Every external result must preserve provider name, provider type,
     external record ID, retrieval timestamp, source reference, query
     context, and authoritative/curated/external classification" (provider
     architecture requirement 8).

Two provenance shapes are supported on the same row, deliberately overlapping
with ProvenanceMixin rather than replacing it:

  - File-sourced evidence (Phase 0 datasets): source_dataset/source_tier/
    source_type from ProvenanceMixin are populated; provider_* fields are
    null.
  - Provider-sourced evidence (local reference lookups today, live external
    APIs in a future phase): provider_name/provider_kind/external_record_id/
    retrieved_at/source_reference/query_context are populated; source_dataset
    is set to the provider name for a human-readable trail, source_tier
    reflects the provider's classification (TIER_1_AUTHORITATIVE /
    TIER_2_CURATED_DEMO / EXTERNAL_LIVE).

`extracted_fact` and `snippet` are deliberately short, structured fields, not
a raw dump of an entire source document -- see the Security Baseline section
of docs/phase-1-foundation.md ("no raw full dataset records in application
logs" applies equally to Evidence rows).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import EvidenceType, ProviderKind
from app.models.base import ProvenanceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.resolution import EntityMatch


class Evidence(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    client: Mapped["Client | None"] = relationship(back_populates="evidence_records")

    evidence_type: Mapped[EvidenceType] = mapped_column(SAEnum(EvidenceType), nullable=False, index=True)

    # What kind of record this evidence is about, and (loosely, non-FK-
    # enforced -- the source table varies) which row, mirroring the
    # EntityMatch subject_type/subject_id pattern.
    source_record_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source_record_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    extracted_fact: Mapped[str] = mapped_column(Text, nullable=False)
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # --- Phase 3 additions ---
    # JSON-encoded structured facts. Phase 1 stored only prose
    # (`extracted_fact`); Phase 3's brief (SS10) requires machine-readable
    # facts alongside it, so a future risk engine can consume evidence without
    # parsing English. Written only via app/services/evidence_service.py,
    # which owns the encoding and the size bound.
    structured_facts: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The evidence graph edge (Phase 3 brief SS11): Client -> Evidence ->
    # Matched Entity -> Source. Nullable because plenty of evidence has
    # nothing to do with a resolution (a transaction typology, a manual note).
    entity_match_id: Mapped[int | None] = mapped_column(
        ForeignKey("entity_matches.id", ondelete="SET NULL"), nullable=True, index=True
    )
    entity_match: Mapped["EntityMatch | None"] = relationship()

    # Which component produced this evidence, e.g. "local_sanctions_provider",
    # "entity_resolution_service". No agent runs in Phase 1; this column just
    # exists so future rows are attributable.
    producing_component: Mapped[str] = mapped_column(String, nullable=False)

    # --- provider-sourced provenance (nullable; populated when evidence came
    # from app/providers/ rather than a static Phase 0 file ingestion) ---
    provider_name: Mapped[str | None] = mapped_column(String, nullable=True)
    provider_kind: Mapped[ProviderKind | None] = mapped_column(SAEnum(ProviderKind), nullable=True)
    external_record_id: Mapped[str | None] = mapped_column(String, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. a URL
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    query_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-serialized, bounded length

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Evidence id={self.id} type={self.evidence_type} client_id={self.client_id}>"
