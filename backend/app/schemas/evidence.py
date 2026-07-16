"""Evidence schema -- mirrors app/models/evidence.py's dual file-sourced /
provider-sourced provenance shape."""

from datetime import datetime

from pydantic import BaseModel

from app.core.enums import EvidenceType, ProviderKind, SourceTier, SourceType
from app.schemas.base import ORMReadModel


class EvidenceCreate(BaseModel):
    client_id: int | None = None
    evidence_type: EvidenceType
    source_record_type: str | None = None
    source_record_id: int | None = None
    extracted_fact: str
    snippet: str | None = None
    confidence: float
    producing_component: str
    source_dataset: str
    source_tier: SourceTier
    source_type: SourceType
    provider_name: str | None = None
    provider_kind: ProviderKind | None = None
    external_record_id: str | None = None
    source_reference: str | None = None
    retrieved_at: datetime | None = None
    query_context: str | None = None


class EvidenceRead(ORMReadModel):
    id: int
    client_id: int | None
    evidence_type: EvidenceType
    extracted_fact: str
    snippet: str | None
    confidence: float
    producing_component: str
    source_dataset: str
    source_tier: SourceTier
    provider_name: str | None
    provider_kind: ProviderKind | None
    external_record_id: str | None
    source_reference: str | None
    retrieved_at: datetime | None
    created_at: datetime
