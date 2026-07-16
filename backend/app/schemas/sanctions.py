"""SanctionsEntity / SanctionsAlias schemas. `source_tier` is always present
and never optional -- this is the API-level enforcement of "never silently
merge curated demo records with authoritative records without provenance"."""

from datetime import date, datetime

from pydantic import BaseModel

from app.core.enums import SourceTier, SourceType
from app.schemas.base import ORMReadModel


class SanctionsAliasCreate(BaseModel):
    alias_name: str
    alias_type: str | None = None


class SanctionsAliasRead(ORMReadModel):
    id: int
    alias_name: str
    alias_type: str | None


class SanctionsEntityCreate(BaseModel):
    external_entity_id: str
    name: str
    entity_type: str | None = None
    program_or_dataset: str | None = None
    country: str | None = None
    birth_date: date | None = None
    remarks: str | None = None
    source_dataset: str
    source_tier: SourceTier
    source_type: SourceType
    aliases: list[SanctionsAliasCreate] = []


class SanctionsEntityRead(ORMReadModel):
    id: int
    external_entity_id: str
    name: str
    entity_type: str | None
    program_or_dataset: str | None
    country: str | None
    birth_date: date | None
    source_dataset: str
    source_tier: SourceTier
    source_type: SourceType
    ingested_at: datetime
    aliases: list[SanctionsAliasRead] = []
