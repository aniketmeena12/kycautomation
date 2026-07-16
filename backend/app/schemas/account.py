"""Account schemas."""

from pydantic import BaseModel

from app.core.enums import SourceTier, SourceType
from app.schemas.base import ORMReadModel


class AccountCreate(BaseModel):
    external_account_number: int
    client_id: int
    source_dataset: str
    source_tier: SourceTier = SourceTier.INTERNAL
    source_type: SourceType = SourceType.INTERNAL_KYC


class AccountRead(ORMReadModel):
    id: int
    external_account_number: int
    client_id: int
    source_dataset: str
    source_tier: SourceTier
