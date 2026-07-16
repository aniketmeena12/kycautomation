"""Persistence layer for Transaction (the shallow, fully-ingested source
only -- see docs/phase-2-ingestion.md. SAML-D rows are never persisted here;
they're served live by app/providers/saml_d_transaction_provider.py)."""

from __future__ import annotations

from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.enums import TransactionSourceType
from app.models.transaction import Transaction


class TransactionRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_external_id(
        self, transaction_source: TransactionSourceType, external_transaction_id: int
    ) -> Transaction | None:
        stmt = select(Transaction).where(
            Transaction.transaction_source == transaction_source,
            Transaction.external_transaction_id == external_transaction_id,
        )
        return self._db.scalars(stmt).one_or_none()

    def list_for_client(self, client_id: int, *, limit: int = 100) -> list[Transaction]:
        stmt = (
            select(Transaction)
            .where(Transaction.client_id == client_id)
            .order_by(Transaction.occurred_at.desc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt))

    def summary_for_client(self, client_id: int) -> dict:
        # cast(..., Integer) is required, not decorative: SQLAlchemy infers a
        # boolean-comparison expression's result type as Boolean, and its
        # Boolean result-value processor coerces ANY non-zero SUM() back to
        # Python True -- silently discarding the actual count. Verified
        # against real data: without the cast, 22 true rows for a real
        # client came back as `True`, not `22`. See docs/ARCHITECTURE_DECISIONS.md.
        flagged = or_(
            Transaction.ofac_match_flag == True,  # noqa: E712
            Transaction.fatf_country_flag == True,  # noqa: E712
            Transaction.structuring_pattern_flag == True,  # noqa: E712
            Transaction.rapid_movement_flag == True,  # noqa: E712
            Transaction.trade_mispricing_flag == True,  # noqa: E712
        )
        stmt = select(
            func.count(Transaction.id),
            func.coalesce(func.sum(Transaction.amount), 0.0),
            func.coalesce(func.sum(cast(flagged, Integer)), 0),
            func.min(Transaction.occurred_at),
            func.max(Transaction.occurred_at),
        ).where(Transaction.client_id == client_id)
        row = self._db.execute(stmt).one()
        count, total_amount, flagged_count, earliest, latest = row
        return {
            "transaction_count": count or 0,
            "total_amount": float(total_amount or 0.0),
            "flagged_count": int(flagged_count or 0),
            "earliest_transaction_at": earliest,
            "latest_transaction_at": latest,
        }

    def upsert(
        self, *, transaction_source: TransactionSourceType, external_transaction_id: int | None, **fields
    ) -> tuple[Transaction, bool]:
        existing = None
        if external_transaction_id is not None:
            existing = self.get_by_external_id(transaction_source, external_transaction_id)

        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing, False

        txn = Transaction(
            transaction_source=transaction_source,
            external_transaction_id=external_transaction_id,
            **fields,
        )
        self._db.add(txn)
        self._db.flush()
        return txn, True

    def count(self) -> int:
        return self._db.scalar(select(func.count()).select_from(Transaction)) or 0
