"""Persistence layer for AuditLog. This is the only code allowed to construct
an AuditLog ORM instance -- app/services/audit_service.py is the only caller,
by convention."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog


class AuditLogRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def create(self, **fields) -> AuditLog:
        entry = AuditLog(**fields)
        self._db.add(entry)
        self._db.commit()
        self._db.refresh(entry)
        return entry

    def list_recent(self, limit: int = 50) -> list[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        return list(self._db.scalars(stmt))
