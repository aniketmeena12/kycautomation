"""
Account -- the bridge entity between Client and deep transaction history.

Per docs/phase-0-dataset-audit.md SS4.2: only 60 of 2,000 clients have any
Account rows at all (client_account_mapping.csv), each with exactly 2. Most
clients will have zero accounts, which is correct, not a data gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import ProvenanceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.client import Client
    from app.models.transaction import Transaction


class Account(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("external_account_number", name="uq_account_external_number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_account_number: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    client_id: Mapped[int] = mapped_column(
        ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )

    client: Mapped["Client"] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Account id={self.id} external_account_number={self.external_account_number}>"
