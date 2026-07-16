"""
Persistence for Case, HumanReview, and SARDraft.

Deliberate omissions, each enforcing a Phase 6 rule -- the technique Phase 4
established (app/repositories/risk_repository.py) and Phase 5 continued:

  * NO update/delete for HumanReview. "Never overwrite reviews" (brief SS4) is
    a write path that does not exist. A reviewer who changes their mind records
    a NEW review; "escalated, then closed an hour later" and "closed" are
    different facts and only the first is true.

  * NO delete for Case, ever. Nothing in a compliance system deletes a case.

  * NO method to set Case.status directly. Status moves only through
    CaseService, which routes every change through the validated state machine.
    A repository that could set status would be a way around the state machine.

  * NO method to set SARStatus.APPROVED. Only CaseService's review handler can,
    and only from a human's APPROVE_DRAFT_SAR.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import CaseStatus, SARStatus
from app.models.case import Case
from app.models.review import HumanReview
from app.models.sar import SARDraft


class CaseRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ---------------- Case ---------------- #

    def get_by_id(self, case_id: int) -> Case | None:
        stmt = (
            select(Case)
            .options(selectinload(Case.reviews), selectinload(Case.sar_drafts), selectinload(Case.client))
            .where(Case.id == case_id)
        )
        return self._db.scalars(stmt).unique().first()

    def get_by_ref(self, case_ref: str) -> Case | None:
        stmt = (
            select(Case)
            .options(selectinload(Case.reviews), selectinload(Case.sar_drafts), selectinload(Case.client))
            .where(Case.case_ref == case_ref)
        )
        return self._db.scalars(stmt).unique().first()

    def latest_open_for_client(self, client_id: int) -> Case | None:
        """The active case for a client, if any. CLOSED is excluded because a
        closed case must never absorb new activity -- that would let a decision
        be recorded against a case whose reviewer already signed it off."""
        stmt = (
            select(Case)
            .options(selectinload(Case.reviews), selectinload(Case.sar_drafts), selectinload(Case.client))
            .where(Case.client_id == client_id, Case.status != CaseStatus.CLOSED)
            .order_by(Case.opened_at.desc(), Case.id.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).unique().first()

    def list(
        self,
        *,
        status: CaseStatus | None = None,
        client_id: int | None = None,
        assigned_to: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Case]:
        stmt = select(Case).options(
            selectinload(Case.reviews), selectinload(Case.sar_drafts), selectinload(Case.client)
        )
        if status is not None:
            stmt = stmt.where(Case.status == status)
        if client_id is not None:
            stmt = stmt.where(Case.client_id == client_id)
        if assigned_to is not None:
            stmt = stmt.where(Case.assigned_to == assigned_to)
        stmt = stmt.order_by(Case.opened_at.desc(), Case.id.desc()).offset(offset).limit(limit)
        return list(self._db.scalars(stmt).unique())

    def count(self, *, status: CaseStatus | None = None) -> int:
        stmt = select(func.count()).select_from(Case)
        if status is not None:
            stmt = stmt.where(Case.status == status)
        return self._db.scalar(stmt) or 0

    def create(self, **fields) -> Case:
        case = Case(**fields)
        self._db.add(case)
        self._db.flush()
        return case

    def next_case_ref(self) -> str:
        """Sequential, human-quotable. Derived from the max id rather than a
        counter table: a compliance officer citing CASE-000123 in an email needs
        it to be stable and unique, not globally ordered across shards -- and
        this is a single-writer SQLite deployment (ADR-001)."""
        highest = self._db.scalar(select(func.max(Case.id))) or 0
        return f"CASE-{highest + 1:06d}"

    # ---------------- HumanReview (append-only) ---------------- #

    def add_review(self, **fields) -> HumanReview:
        review = HumanReview(**fields)
        self._db.add(review)
        self._db.flush()
        return review

    def list_reviews(self, case_id: int) -> list[HumanReview]:
        stmt = (
            select(HumanReview)
            .where(HumanReview.case_id == case_id)
            .order_by(HumanReview.decided_at, HumanReview.id)
        )
        return list(self._db.scalars(stmt))

    def count_reviews(self) -> int:
        return self._db.scalar(select(func.count()).select_from(HumanReview)) or 0

    def review_counts_by_action(self) -> dict[str, int]:
        stmt = select(HumanReview.action, func.count()).group_by(HumanReview.action)
        return {action.value: count for action, count in self._db.execute(stmt)}

    # ---------------- SARDraft ---------------- #

    def add_sar(self, **fields) -> SARDraft:
        sar = SARDraft(**fields)
        self._db.add(sar)
        self._db.flush()
        return sar

    def get_sar(self, sar_id: int) -> SARDraft | None:
        return self._db.get(SARDraft, sar_id)

    def latest_sar_for_case(self, case_id: int) -> SARDraft | None:
        stmt = (
            select(SARDraft)
            .where(SARDraft.case_id == case_id)
            .order_by(SARDraft.generated_at.desc(), SARDraft.id.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def list_sars_for_case(self, case_id: int) -> list[SARDraft]:
        stmt = (
            select(SARDraft)
            .where(SARDraft.case_id == case_id)
            .order_by(SARDraft.generated_at.desc(), SARDraft.id.desc())
        )
        return list(self._db.scalars(stmt))

    def count_sars(self, *, status: SARStatus | None = None) -> int:
        stmt = select(func.count()).select_from(SARDraft)
        if status is not None:
            stmt = stmt.where(SARDraft.status == status)
        return self._db.scalar(stmt) or 0


__all__ = ["CaseRepository"]
