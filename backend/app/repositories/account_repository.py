"""Persistence layer for Account.

Like ClientRepository, upsert() only flushes (not commits) -- bulk loaders
control transaction boundaries so a whole file ingests atomically. See
app/ingestion/loaders/base.py.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import Account


class AccountRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_external_number(self, external_account_number: int) -> Account | None:
        stmt = select(Account).where(Account.external_account_number == external_account_number)
        return self._db.scalars(stmt).one_or_none()

    def list_for_client(self, client_id: int) -> list[Account]:
        stmt = select(Account).where(Account.client_id == client_id)
        return list(self._db.scalars(stmt))

    def upsert(self, *, external_account_number: int, **fields) -> tuple[Account, bool]:
        existing = self.get_by_external_number(external_account_number)
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._db.flush()
            return existing, False

        account = Account(external_account_number=external_account_number, **fields)
        self._db.add(account)
        self._db.flush()
        return account, True
