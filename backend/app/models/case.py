"""
Case -- the compliance workspace (Phase 6 brief SS2).

A Case does NOT own data. It is an anchor: one row per client-under-review,
around which the evidence, matches, events, snapshots, alerts, investigations,
reviews, and SAR drafts that ALREADY EXIST are aggregated by CaseService.

That is deliberate, and it is the single most important design decision in this
phase. The obvious alternative -- copying investigation summaries, risk scores,
and evidence text onto the case for convenient reads -- would create a second
source of truth that silently drifts from the first. A compliance workspace
showing a stale score next to a live investigation is worse than no workspace.
So the Case stores only what is genuinely ITS OWN: lifecycle state, who is
handling it, and when it opened and closed. Everything else is read through a
foreign key at request time.

`status` is the only mutable field, and it moves only through
app/casework/state_machine.py, which validates every transition. There is no
`update()` on the repository for anything else.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import CaseStatus
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.review import HumanReview
    from app.models.sar import SARDraft


class Case(Base, TimestampMixin):
    __tablename__ = "cases"
    __table_args__ = (
        # One OPEN-or-active case per client is NOT enforced here, deliberately:
        # a client legitimately has a history of closed cases, and a unique
        # constraint on client_id alone would make the second case impossible.
        # `case_ref` is the natural key -- human-quotable and stable.
        UniqueConstraint("case_ref", name="uq_case_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # A human-quotable reference. Compliance officers cite case numbers in
    # emails and filings; an autoincrement integer is an implementation detail
    # that should never have to appear in a regulator's inbox.
    case_ref: Mapped[str] = mapped_column(String, nullable=False, index=True)

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client: Mapped["Client"] = relationship()

    status: Mapped[CaseStatus] = mapped_column(
        SAEnum(CaseStatus), nullable=False, default=CaseStatus.OPEN, index=True
    )

    title: Mapped[str | None] = mapped_column(String, nullable=True)
    opened_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which alert or investigation caused this case to exist. Nullable: a case
    # can also be opened manually by a reviewer.
    opening_alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True
    )
    opening_investigation_id: Mapped[int | None] = mapped_column(
        ForeignKey("investigations.id", ondelete="SET NULL"), nullable=True
    )

    assigned_to: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free text, human-supplied. Only ever set alongside a CLOSE_CASE review, so
    # a closed case always points at the person and reasoning that closed it.
    closed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Append-only children. Cascade on delete only so a deleted client does not
    # leave orphans -- nothing in the application ever deletes a case.
    reviews: Mapped[list["HumanReview"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="HumanReview.decided_at"
    )
    sar_drafts: Mapped[list["SARDraft"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", order_by="SARDraft.generated_at"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Case {self.case_ref} client_id={self.client_id} status={self.status}>"
