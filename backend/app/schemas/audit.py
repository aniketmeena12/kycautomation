"""AuditLog schemas. AuditEventCreate is the internal contract used by
app/services/audit_service.py.record_audit_event -- never expose an endpoint
that lets a caller construct an arbitrary AuditLog row directly with a
free-form actor_id; always go through the service."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.enums import ActorType
from app.schemas.base import ORMReadModel


class AuditEventCreate(BaseModel):
    actor_type: ActorType
    actor_id: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    reason: str | None = None
    old_value: Any = None
    new_value: Any = None
    correlation_id: str | None = None


class AuditLogRead(ORMReadModel):
    id: int
    actor_type: ActorType
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    old_value: str | None
    new_value: str | None
    reason: str | None
    correlation_id: str | None
    created_at: datetime
