"""
Persistence for Investigation, InvestigationFinding, and
InvestigationRecommendation.

Two deliberate omissions, each enforcing a Phase 5 rule -- the same technique
Phase 4 used in app/repositories/risk_repository.py, where the absence of an
`update` is what actually makes risk events immutable:

  * There is NO method to update a finding, a recommendation, or a stored
    report. What the agent said is what the agent said. Rewriting an
    investigation after the fact would destroy the audit trail it exists to
    provide, and a "corrected" report nobody can diff against the original is
    not a correction -- it is a cover-up. A re-run creates a NEW row (see
    `rerun` in the orchestrator).

  * There is NO method to set status to ESCALATED or CLOSED. Those are human
    decisions (brief: "Human review must remain part of consequential
    compliance decisions"), and this phase deliberately builds no human-review
    workflow. A repository that cannot express the write is a stronger
    guarantee than a service that merely declines to call it -- the same
    reasoning that keeps CONFIRMED out of the resolution engine (ADR-016).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.investigation import Investigation, InvestigationFinding, InvestigationRecommendation


class InvestigationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, investigation_id: int) -> Investigation | None:
        stmt = (
            select(Investigation)
            .options(
                selectinload(Investigation.findings),
                selectinload(Investigation.recommendations),
            )
            .where(Investigation.id == investigation_id)
        )
        return self._db.scalars(stmt).unique().first()

    def list_for_client(self, client_id: int, *, limit: int = 50, offset: int = 0) -> list[Investigation]:
        stmt = (
            select(Investigation)
            .options(
                selectinload(Investigation.findings),
                selectinload(Investigation.recommendations),
            )
            .where(Investigation.client_id == client_id)
            .order_by(Investigation.opened_at.desc(), Investigation.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self._db.scalars(stmt).unique())

    def count_for_client(self, client_id: int) -> int:
        return (
            self._db.scalar(
                select(func.count()).select_from(Investigation).where(Investigation.client_id == client_id)
            )
            or 0
        )

    def latest_for_context_hash(self, client_id: int, context_hash: str) -> Investigation | None:
        """The most recent investigation run against an identical context.

        Lets `rerun` report whether the evidence actually changed since the
        previous run. A new report over an unchanged context is the model
        being non-deterministic; a new report over a changed context is the
        system working. Without the hash those look the same.
        """
        stmt = (
            select(Investigation)
            .where(Investigation.client_id == client_id, Investigation.context_hash == context_hash)
            .order_by(Investigation.opened_at.desc(), Investigation.id.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def create(self, **fields) -> Investigation:
        investigation = Investigation(**fields)
        self._db.add(investigation)
        self._db.flush()
        return investigation

    def add_finding(self, **fields) -> InvestigationFinding:
        finding = InvestigationFinding(**fields)
        self._db.add(finding)
        self._db.flush()
        return finding

    def add_recommendation(self, **fields) -> InvestigationRecommendation:
        recommendation = InvestigationRecommendation(**fields)
        self._db.add(recommendation)
        self._db.flush()
        return recommendation


__all__ = ["InvestigationRepository"]
