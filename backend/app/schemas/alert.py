"""Alert read schema."""

from datetime import datetime

from app.core.enums import AlertStatus
from app.schemas.base import ORMReadModel


class AlertRead(ORMReadModel):
    id: int
    client_id: int
    status: AlertStatus
    triggering_risk_event_id: int | None
    opened_at: datetime
    closed_at: datetime | None
