"""
Alert -- a client-facing queue entry, distinct from an Investigation (an
alert can exist and be dismissed without ever opening a full investigation).

Phase 1 created the table; Phase 4 populates and extends it additively.

`severity` is deliberately separate from the triggering event's severity and
from the client's risk band: an alert's urgency is about *the change*, not
the absolute state. A client sitting at CRITICAL for a month with nothing new
should not re-alert; a jump from LOW to HIGH should, loudly.

`dedup_key` prevents alert spam (Phase 4 brief SS9: "Avoid duplicate
alerts"), and is enforced by a DB unique constraint per client rather than
only by a pre-check.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Table, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import AlertStatus, AlertTrigger, RiskBand
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.risk import RiskEvent

alert_risk_event = Table(
    "alert_risk_event",
    Base.metadata,
    Column("alert_id", ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True),
    Column("risk_event_id", ForeignKey("risk_events.id", ondelete="CASCADE"), primary_key=True),
)

alert_evidence = Table(
    "alert_evidence",
    Base.metadata,
    Column("alert_id", ForeignKey("alerts.id", ondelete="CASCADE"), primary_key=True),
    Column("evidence_id", ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True),
)


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("client_id", "dedup_key", name="uq_alert_client_dedup"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client: Mapped["Client"] = relationship()

    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(AlertStatus), nullable=False, default=AlertStatus.OPEN, index=True
    )

    # --- Phase 4 additions ---
    severity: Mapped[RiskBand] = mapped_column(
        SAEnum(RiskBand), nullable=False, default=RiskBand.MEDIUM, index=True
    )
    trigger: Mapped[AlertTrigger] = mapped_column(
        SAEnum(AlertTrigger), nullable=False, default=AlertTrigger.SCORE_DELTA
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    dedup_key: Mapped[str] = mapped_column(String, nullable=False, index=True)

    triggering_risk_event_id: Mapped[int | None] = mapped_column(ForeignKey("risk_events.id"), nullable=True)
    triggering_risk_event: Mapped["RiskEvent | None"] = relationship()

    # An alert usually cites several events/evidence items, not just one --
    # the single FK above is kept for the primary trigger, these are the
    # full sets.
    risk_events = relationship("RiskEvent", secondary=alert_risk_event)
    evidence = relationship("Evidence", secondary=alert_evidence)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<Alert id={self.id} client_id={self.client_id} severity={self.severity} trigger={self.trigger}>"
        )
