"""
Investigation / InvestigationFinding / InvestigationRecommendation -- the
persisted output of the Autonomous Investigation Engine.

Phase 1 defined `Investigation` and `InvestigationFinding` as schema-only
contracts, with `summary` explicitly reserved for "the future Investigation
Agent". Phase 5 is that future. The Phase 1 columns are kept EXACTLY as they
were and the new ones are additive and nullable -- the same discipline Phase 4
used when extending RiskEvent. A schema-only contract is only worth writing if
the phase that fills it in honours it rather than rewriting it.

WHAT IS STORED, AND WHAT IS DELIBERATELY NOT
---------------------------------------------
Stored (brief SS9/SS10): context hash, prompt version, model, provider, latency,
token usage, the generated report verbatim, citations, grounding outcome, and
generation timestamp.

NOT stored: chain-of-thought. Not because a filter strips it, but because it is
never requested -- app/providers/anthropic_llm_provider.py pins
`thinking.display` to "omitted", so no reasoning is ever received. There is no
column for it here and nothing to put in one. See ADR-026.

`context_hash` is a fingerprint of the exact context the model saw. It is what
makes `POST /investigations/{id}/rerun` meaningful: two runs with the same hash
were shown identical evidence, so any difference in their reports is the
model's variance, not the data's. Without it, "the agent changed its mind"
and "the evidence changed" are indistinguishable.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import (
    GroundingStatus,
    InvestigationFindingType,
    InvestigationRecommendationAction,
    InvestigationStatus,
)
from app.models.base import TimestampMixin, utcnow

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.risk import RiskScoreSnapshot


class Investigation(Base, TimestampMixin):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client: Mapped["Client"] = relationship()

    status: Mapped[InvestigationStatus] = mapped_column(
        SAEnum(InvestigationStatus), nullable=False, default=InvestigationStatus.OPEN, index=True
    )

    trigger_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("risk_score_snapshots.id"), nullable=True
    )
    trigger_snapshot: Mapped["RiskScoreSnapshot | None"] = relationship()

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Phase 1 column, now populated by the agent (its docstring said it would be).
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---------------- Phase 5 additions (all nullable) ---------------- #

    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    triggering_alert_id: Mapped[int | None] = mapped_column(
        ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # --- Reproducibility ---
    context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- Which model produced this, on which provider ---
    llm_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String, nullable=True)

    # --- Operational metrics (brief SS10) ---
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Null on every current model: sampling parameters are REJECTED with HTTP
    # 400 rather than defaulted, so no temperature is sent. Null is the honest
    # record of a parameter that was never part of the request; writing 0.0
    # would fabricate a request field. Kept because a future provider may
    # legitimately use one. See ADR-025.
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)

    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- The report, verbatim ---
    # JSON-encoded InvestigationReport, exactly as validated. Stored whole so a
    # report can be re-read as issued even after the evidence behind it changes
    # -- the same reason Phase 4 persists factor_contributions on the snapshot
    # rather than recomputing them on read.
    report_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Grounding outcome (deterministic; see app/investigation/grounding.py) ---
    grounding_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Count, not a flag: 1 fabricated citation and 12 are different situations.
    hallucinated_citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_used_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_available_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grounding_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Injection patterns found in this client's untrusted evidence text.
    injection_flags_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    findings: Mapped[list["InvestigationFinding"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )
    recommendations: Mapped[list["InvestigationRecommendation"]] = relationship(
        back_populates="investigation", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Investigation id={self.id} client_id={self.client_id} status={self.status}>"


class InvestigationFinding(Base, TimestampMixin):
    __tablename__ = "investigation_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Phase 1's single FK -- kept, and set to the finding's FIRST VALID
    # citation. It is the real referential-integrity edge into the evidence
    # graph (Client -> Evidence -> EntityMatch -> Source), which a JSON blob
    # cannot provide. The full citation list lives in cited_evidence_ids_json
    # alongside it; neither replaces the other.
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("evidence.id"), nullable=True)

    finding_text: Mapped[str] = mapped_column(Text, nullable=False)

    # ---------------- Phase 5 additions ---------------- #
    finding_type: Mapped[InvestigationFindingType | None] = mapped_column(
        SAEnum(InvestigationFindingType), nullable=True, index=True
    )
    cited_evidence_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_statement: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Deterministic verdict from grounding.py. An UNGROUNDED finding is stored,
    # not deleted: erasing it would erase the evidence that the model
    # hallucinated -- the single most important thing a reviewer could learn
    # from this run. We flag defects; we do not tidy them away.
    grounding_status: Mapped[GroundingStatus | None] = mapped_column(
        SAEnum(GroundingStatus), nullable=True, index=True
    )
    invalid_evidence_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    investigation: Mapped["Investigation"] = relationship(back_populates="findings")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<InvestigationFinding id={self.id} investigation_id={self.investigation_id}>"


class InvestigationRecommendation(Base, TimestampMixin):
    """A recommended NEXT STEP -- never a decision.

    `action` is enum-constrained, and InvestigationRecommendationAction has no
    APPROVE and no REJECT. The permitted vocabulary is therefore enforced by
    the database column type, by the Pydantic layer, by the JSON schema handed
    to the model, and by an explicit check in grounding.py. A recommendation to
    approve or reject a client is unrepresentable at every layer, which is what
    "humans make the final compliance decision" has to mean if it is to mean
    anything. See ADR-027.
    """

    __tablename__ = "investigation_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False, index=True
    )

    action: Mapped[InvestigationRecommendationAction] = mapped_column(
        SAEnum(InvestigationRecommendationAction), nullable=False, index=True
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    cited_evidence_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    investigation: Mapped["Investigation"] = relationship(back_populates="recommendations")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<InvestigationRecommendation id={self.id} action={self.action}>"
