"""AuditLog -- append-only trail of every alert, evidence item, AI decision,
score change, investigation step, and reviewer action. Written exclusively
through app/services/audit_service.py (see app/repositories/audit_repository.py
for the persistence layer). No code path should insert an AuditLog row via
any other route, so every audit entry goes through the same validation and
truncation safeguards."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.enums import ActorType
from app.models.base import utcnow


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    actor_type: Mapped[ActorType] = mapped_column(SAEnum(ActorType), nullable=False, index=True)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)

    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # JSON-serialized (via json.dumps(default=str)) and length-bounded by
    # the audit service -- never a raw dataset row or secret. See
    # app/services/audit_service.py MAX_VALUE_LENGTH.
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog id={self.id} actor={self.actor_type}:{self.actor_id} action={self.action!r}>"
