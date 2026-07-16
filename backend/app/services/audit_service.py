"""
Audit logging service. Every future service (entity resolution, risk scoring,
investigation, review workflow) should call record_audit_event(...) rather
than writing an AuditLog row directly -- this is the one place that enforces
the safety rules from the Security Baseline:

  - old_value/new_value are JSON-serialized safely (never str() on an
    arbitrary object that might repr() something sensitive) and truncated to
    MAX_VALUE_LENGTH so a caller can never accidentally dump an entire raw
    dataset row (e.g. a full SanctionsEntity.remarks blob) into the audit log.
  - actor_type is always a real ActorType enum value -- never a free-form
    string that could later be confused with SQL or logged unsafely.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.enums import ActorType
from app.models.audit import AuditLog
from app.repositories.audit_repository import AuditLogRepository

MAX_VALUE_LENGTH = 2000


def _safe_serialize(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, default=str)
    except (TypeError, ValueError):
        serialized = str(value)
    if len(serialized) > MAX_VALUE_LENGTH:
        return serialized[:MAX_VALUE_LENGTH] + f"... [truncated, {len(serialized)} chars total]"
    return serialized


def record_audit_event(
    db: Session,
    *,
    actor_type: ActorType,
    actor_id: str | None = None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    reason: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    correlation_id: str | None = None,
) -> AuditLog:
    repo = AuditLogRepository(db)
    return repo.create(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        reason=reason,
        old_value=_safe_serialize(old_value),
        new_value=_safe_serialize(new_value),
        correlation_id=correlation_id,
    )
