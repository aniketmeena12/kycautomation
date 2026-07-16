"""
Transaction -- unified over two non-overlapping source datasets.

Per docs/data-dictionary.md, `transactions_with_fatf_ofac.csv` (shallow, all
2,000 clients, pre-computed typology flags, keyed by client_id) and SAML-D.csv
(deep, only the 120 mapped accounts, ground-truth Is_laundering label, keyed
by account) cover different calendar periods and carry genuinely different
fields. Rather than force one lossy universal schema, common fields are
normalized and source-specific fields are kept nullable and grouped, with
`source_type` as the discriminator -- see docs/system-design-phase-0.md SS4.

Phase 1 defines this schema only. No SAML-D rows are loaded in this phase
(951 MB / 9.5M rows -- see docs/phase-0-dataset-audit.md SS11 and
docs/phase-1-foundation.md for why that is deliberately deferred).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import TransactionSourceType
from app.models.base import ProvenanceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.client import Client


class Transaction(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "transactions"
    __table_args__ = (
        # Matches the documented natural key (app/ingestion/base.py) for the
        # shallow file exactly, and doubles as the composite index the
        # upsert lookup needs -- see docs/ARCHITECTURE_DECISIONS.md ADR-006.
        # SQLite treats NULL != NULL in a unique index, so this does not
        # restrict SAML-D rows (external_transaction_id is always NULL for
        # those) to a single row.
        UniqueConstraint(
            "transaction_source", "external_transaction_id", name="uq_transaction_source_external_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Present for the shallow file; absent (None) for SAML-D, which has no native row ID.
    external_transaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # No standalone index here deliberately: transaction_source has only 2
    # distinct values, so a single-column index on it is low-value and, per
    # docs/ARCHITECTURE_DECISIONS.md ADR-006, actively misled SQLite's
    # planner into using it (and full-scanning every matching row) instead
    # of the composite unique index above for the upsert lookup query.
    transaction_source: Mapped[TransactionSourceType] = mapped_column(
        SAEnum(TransactionSourceType), nullable=False
    )

    # Nullable: the shallow file keys by client_id only; SAML-D keys by account only.
    # Resolving a SAML-D row to a client happens via Account, not directly.
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), nullable=True, index=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)

    # --- normalized common fields ---
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)  # shallow file has no currency field
    transaction_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # free text: vocab differs per source
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    # --- shallow-file-specific fields (transactions_with_fatf_ofac.csv) ---
    client_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    counterparty_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    ofac_match_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fatf_country_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    structuring_pattern_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    rapid_movement_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    trade_mispricing_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # --- SAML-D-specific fields ---
    sender_account_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    receiver_account_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_bank_location: Mapped[str | None] = mapped_column(String, nullable=True)
    receiver_bank_location: Mapped[str | None] = mapped_column(String, nullable=True)
    is_laundering: Mapped[bool | None] = mapped_column(Boolean, nullable=True, index=True)
    laundering_type: Mapped[str | None] = mapped_column(String, nullable=True)

    client: Mapped["Client | None"] = relationship(back_populates="transactions")
    account: Mapped["Account | None"] = relationship(back_populates="transactions")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Transaction id={self.id} source={self.transaction_source} amount={self.amount}>"
