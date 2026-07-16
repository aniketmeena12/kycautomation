"""
Client -- the primary monitored entity (docs/phase-0-dataset-audit.md SS3/SS4.1).

`id` is the internal stable primary key every other table references.
`external_client_id` preserves the original `client_id` from
clients_with_fatf_ofac.csv so provenance back to the source row is never lost,
per the Phase 1 requirement to decouple internal IDs from source IDs.

All *_flag and ownership_opacity_score fields are carried through as-is from
the source dataset. Per docs/data-dictionary.md, these are UPSTREAM LABELS,
not something this system derives -- nothing in this model computes them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.enums import ClientType, SectorRisk
from app.models.base import ProvenanceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.evidence import Evidence
    from app.models.transaction import Transaction


class Client(Base, TimestampMixin, ProvenanceMixin):
    __tablename__ = "clients"
    __table_args__ = (UniqueConstraint("external_client_id", name="uq_client_external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_client_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    client_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    client_type: Mapped[ClientType] = mapped_column(SAEnum(ClientType), nullable=False)
    sector: Mapped[str] = mapped_column(String, nullable=False)
    sector_risk: Mapped[SectorRisk] = mapped_column(SAEnum(SectorRisk), nullable=False)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)

    pep_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sanctions_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fatf_country_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ofac_country_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sectoral_sanctions_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ownership_opacity_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    accounts: Mapped[list["Account"]] = relationship(back_populates="client", cascade="all, delete-orphan")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="client")
    evidence_records: Mapped[list["Evidence"]] = relationship(back_populates="client")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Client id={self.id} external_client_id={self.external_client_id} name={self.client_name!r}>"
