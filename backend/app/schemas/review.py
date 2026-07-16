"""HumanReview schemas."""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import ReviewAction
from app.schemas.base import ORMReadModel


class HumanReviewCreate(BaseModel):
    investigation_id: int | None = None
    alert_id: int | None = None
    reviewer_name: str
    action: ReviewAction
    rationale: str | None = None


class HumanReviewRead(ORMReadModel):
    id: int
    investigation_id: int | None
    alert_id: int | None
    reviewer_name: str
    action: ReviewAction
    rationale: str | None
    decided_at: datetime
