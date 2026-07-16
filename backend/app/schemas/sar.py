"""SARDraft schema. `status` is always DRAFT-family in Phase 1; nothing in
this codebase files a SAR automatically -- see app/models/sar.py."""

from datetime import datetime

from app.core.enums import SARStatus
from app.schemas.base import ORMReadModel


class SARDraftRead(ORMReadModel):
    id: int
    client_id: int
    investigation_id: int | None
    status: SARStatus
    content: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
