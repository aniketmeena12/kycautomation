"""
SARDraft -- explicitly DRAFT ONLY.

Phase 1 wrote: "Nothing in this schema or Phase 1 code ever transitions a SAR to
a filed state automatically; `status` starts and stays at DRAFT until a human
reviewer acts." Phase 6 implements the drafting and honours that sentence
exactly: no automated path sets APPROVED, and there is no FILED state at all
because this system does not file.

Phase 1's columns are kept verbatim; Phase 6's are additive and nullable.

WHY THE SECTIONS ARE STORED AS RENDERED JSON, NOT REGENERATED ON READ
---------------------------------------------------------------------
A SAR is a point-in-time assertion about what was known when a human signed it.
If reading a SAR re-derived its chronology and evidence from live tables, then
a SAR approved on Monday could show different facts on Tuesday because the
client was re-monitored -- and the reviewer's approval would attach to a
document that no longer exists. So the draft is frozen at generation. This is
the same reasoning that makes Phase 4 persist `factor_contributions` on the
snapshot rather than recompute them (ADR-017 lineage), and Phase 5 store the
report verbatim.

`sections_json` is therefore the SAR. Everything else on this row is metadata
about how it came to exist.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import SARStatus
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.client import Client
    from app.models.investigation import Investigation


class SARDraft(Base, TimestampMixin):
    __tablename__ = "sar_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client: Mapped["Client"] = relationship()

    investigation_id: Mapped[int | None] = mapped_column(ForeignKey("investigations.id"), nullable=True)
    investigation: Mapped["Investigation | None"] = relationship()

    status: Mapped[SARStatus] = mapped_column(SAEnum(SARStatus), nullable=False, default=SARStatus.DRAFT)

    # Phase 1's column -- the human-readable rendering of the draft.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)

    reviewed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ---------------- Phase 6 additions (all nullable) ---------------- #

    case_id: Mapped[int | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    case: Mapped["Case | None"] = relationship(back_populates="sar_drafts")

    sar_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # The nine sections of brief SS6, frozen at generation time.
    sections_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Every evidence id the draft cites, and the deterministic grounding
    # verdict over them. A SAR that cites evidence which does not exist is the
    # single worst artefact this system could produce -- it is a regulatory
    # filing built on a fabrication -- so the same validator that guards
    # investigations (app/investigation/grounding.py) guards this too.
    cited_evidence_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    grounding_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hallucinated_citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Which parts came from a model. Only the narrative ever does; every factual
    # section is assembled deterministically from stored rows. Recorded so a
    # reviewer can see exactly how much of what they are signing was written by
    # a machine.
    narrative_generated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    narrative_model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    narrative_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SARDraft id={self.id} ref={self.sar_ref} status={self.status}>"
