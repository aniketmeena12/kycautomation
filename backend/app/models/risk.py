"""
RiskEvent / RiskScoreSnapshot -- the deterministic scoring records.

Phase 1 created these as empty contracts. Phase 4 populates them and extends
them additively; no column was removed or repurposed.

RISK EVENTS ARE IMMUTABLE (Phase 4 brief SS3). There is no update path in
app/repositories/risk_event_repository.py -- only insert and read. `status`
exists for a later human-review phase to act on; the monitoring engine itself
never rewrites an event. An observation that was true when observed stays on
the record.

`dedup_key` is the change-detection mechanism (brief SS9): a stable
fingerprint of the FINDING (never a timestamp), unique per client. Re-running
a monitoring cycle over unchanged data yields the same keys, so no duplicate
event is created and no duplicate alert fires.

RiskScoreSnapshot IS the risk history (brief SS6) -- it already carried
previous_score / current_score / computed_at / trigger_reason /
triggering_events from Phase 1, so Phase 4 added `delta` and
`factor_contributions` rather than building a parallel table. Snapshots are
append-only: every recalculation inserts a new row, nothing is overwritten.

`scoring_logic_version` is now real -- it comes from
config/risk_factors.json's `scoring.scoring_logic_version`, so every stored
score traces to the exact formula version and registry that produced it.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import RiskBand, RiskEventStatus, RiskEventType
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.client import Client

risk_event_evidence = Table(
    "risk_event_evidence",
    Base.metadata,
    Column("risk_event_id", ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True),
    Column("evidence_id", ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True),
)

risk_snapshot_trigger_event = Table(
    "risk_snapshot_trigger_event",
    Base.metadata,
    Column("snapshot_id", ForeignKey("risk_score_snapshots.id", ondelete="CASCADE"), primary_key=True),
    Column("risk_event_id", ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True),
)


class RiskEvent(Base, TimestampMixin):
    __tablename__ = "risk_events"
    __table_args__ = (
        # Change detection: the same finding can only ever exist once per
        # client. Enforced by the DB, not just by a pre-check, so a race or a
        # future careless caller cannot create a duplicate.
        UniqueConstraint("client_id", "dedup_key", name="uq_risk_event_client_dedup"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client: Mapped["Client"] = relationship()

    event_type: Mapped[RiskEventType] = mapped_column(SAEnum(RiskEventType), nullable=False, index=True)
    severity: Mapped[RiskBand] = mapped_column(SAEnum(RiskBand), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[RiskEventStatus] = mapped_column(
        SAEnum(RiskEventStatus), nullable=False, default=RiskEventStatus.OPEN
    )

    # --- Phase 4 additions ---
    dedup_key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    trigger: Mapped[str | None] = mapped_column(String, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # The factor that classified this signal, so an event traces to the
    # registry entry that produced it.
    factor_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    event_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    evidence = relationship("Evidence", secondary=risk_event_evidence)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RiskEvent id={self.id} type={self.event_type} client_id={self.client_id} key={self.dedup_key!r}>"


class RiskScoreSnapshot(Base, TimestampMixin):
    __tablename__ = "risk_score_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client: Mapped["Client"] = relationship()

    previous_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[RiskBand] = mapped_column(SAEnum(RiskBand), nullable=False)

    # --- Phase 4 additions ---
    previous_band: Mapped[RiskBand | None] = mapped_column(SAEnum(RiskBand), nullable=True)
    # Stored rather than derived: `previous_score` can be NULL on a first
    # snapshot, and a reader shouldn't have to special-case that arithmetic.
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    # JSON-encoded list of FactorContribution. Persisted so a score explains
    # itself later WITHOUT re-running the engine against data that may since
    # have changed -- the same discipline as EntityMatch.reasons (Phase 3).
    factor_contributions: Mapped[str | None] = mapped_column(Text, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Real as of Phase 4: sourced from config/risk_factors.json's
    # scoring.scoring_logic_version, so a stored score traces to the formula
    # version that produced it.
    scoring_logic_version: Mapped[str | None] = mapped_column(String, nullable=True)

    triggering_events = relationship("RiskEvent", secondary=risk_snapshot_trigger_event)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<RiskScoreSnapshot id={self.id} client_id={self.client_id} score={self.current_score}>"
