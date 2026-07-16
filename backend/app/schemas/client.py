"""Client schemas. ClientCreate is the validated input contract a future
ingestion job will use before constructing a Client ORM row (docs/data-
dictionary.md's clients_with_fatf_ofac.csv column set, normalized)."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import ClientType, SectorRisk, SourceTier, SourceType
from app.schemas.base import ORMReadModel


class ClientCreate(BaseModel):
    external_client_id: int
    client_name: str
    client_type: ClientType
    sector: str
    sector_risk: SectorRisk
    country: str = Field(min_length=2, max_length=2)
    pep_flag: bool = False
    sanctions_flag: bool = False
    fatf_country_flag: bool = False
    ofac_country_flag: bool = False
    sectoral_sanctions_flag: bool = False
    ownership_opacity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_dataset: str
    source_tier: SourceTier = SourceTier.INTERNAL
    source_type: SourceType = SourceType.INTERNAL_KYC


class ClientRead(ORMReadModel):
    id: int
    external_client_id: int
    client_name: str
    client_type: ClientType
    sector: str
    sector_risk: SectorRisk
    country: str
    pep_flag: bool
    sanctions_flag: bool
    fatf_country_flag: bool
    ofac_country_flag: bool
    sectoral_sanctions_flag: bool
    ownership_opacity_score: float
    source_dataset: str
    source_tier: SourceTier
    ingested_at: datetime
