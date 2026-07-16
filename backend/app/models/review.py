"""
HumanReview -- the record of a human compliance decision. Every consequential
action (SAR sign-off, investigation closure, case closure) must trace back to
one of these rows, per the "human review must remain part of consequential
compliance decisions" project rule.

Phase 1 defined this table and reserved it. Phase 6 fills it in, keeping every
Phase 1 column exactly as it was and adding the brief's SS4 fields (case link,
previous state, new state) as nullable -- the same additive discipline Phases 4
and 5 used.

APPEND-ONLY, ENFORCED BY OMISSION
----------------------------------
"Never overwrite reviews" (brief SS4) is not a rule this code politely follows;
it is a write path that does not exist. CaseRepository has no method to update
or delete a review. A reviewer who changes their mind records a NEW review --
which is the honest artefact, because "the reviewer escalated, then closed it
an hour later" and "the reviewer closed it" are different facts, and only the
first is true.

This is the same technique that keeps `update` off RiskEventRepository (Phase 4)
and off InvestigationRepository (ADR-029).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import CaseStatus, ReviewAction
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.alert import Alert
    from app.models.case import Case
    from app.models.investigation import Investigation


class HumanReview(Base, TimestampMixin):
    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    investigation_id: Mapped[int | None] = mapped_column(ForeignKey("investigations.id"), nullable=True)
    investigation: Mapped["Investigation | None"] = relationship()

    alert_id: Mapped[int | None] = mapped_column(ForeignKey("alerts.id"), nullable=True)
    alert: Mapped["Alert | None"] = relationship()

    reviewer_name: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[ReviewAction] = mapped_column(SAEnum(ReviewAction), nullable=False)
    # Phase 1's column. The API exposes it as `comment` (the brief's word); the
    # column keeps its original name so existing rows and queries stay valid.
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # ---------------- Phase 6 additions (all nullable) ---------------- #

    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    case: Mapped["Case | None"] = relationship(back_populates="reviews")

    # The state transition this review CAUSED. Stored on the review rather than
    # derived later, because deriving it would require replaying every review in
    # order and trusting that nothing else ever touched the status -- the review
    # is the only thing that moves a case, so it is the only honest place to
    # record what it moved.
    previous_state: Mapped[CaseStatus | None] = mapped_column(SAEnum(CaseStatus), nullable=True)
    new_state: Mapped[CaseStatus | None] = mapped_column(SAEnum(CaseStatus), nullable=True)

    # What the action operated on, when it targets a specific record
    # (e.g. CONFIRM_MATCH -> an EntityMatch id, APPROVE_DRAFT_SAR -> a SAR id).
    target_type: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    correlation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<HumanReview id={self.id} reviewer={self.reviewer_name!r} action={self.action}>"
