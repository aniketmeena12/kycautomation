"""API contracts for entity resolution and evidence."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.core.enums import EntityMatchStatus, EntityMatchSubjectType, EvidenceType, SourceTier
from app.resolution.schemas import EntityResolutionResult, ResolutionRunResult, ResolutionSubject
from app.schemas.base import ORMReadModel


class ResolvePairRequest(BaseModel):
    """Score two supplied entities directly -- no DB, no providers.
    The purest expression of the engine's genericity: any two entities, from
    anywhere, including ones this system has never seen."""

    subject: ResolutionSubject
    candidate: ResolutionSubject


class ResolveSubjectRequest(BaseModel):
    """Resolve one subject against generated candidates.

    Supply EITHER `subject` directly, OR a `client_id` / `ownership_entity_id`
    to have the subject built from stored data via the adapters.
    """

    subject: ResolutionSubject | None = None
    client_id: int | None = Field(
        default=None, description="External client_id, as used elsewhere in the API."
    )
    ownership_entity_id: int | None = Field(default=None, description="Internal OwnershipEntity id.")

    include_local_db: bool = True
    include_providers: bool = False
    allow_expensive_providers: bool = Field(
        default=False,
        description=(
            "Permit the Tier-1 OpenSanctions provider (~40-45s over 1.3M rows -- "
            "docs/phase-2-ingestion.md SS3). Off by default."
        ),
    )
    source_tier: SourceTier | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=100.0)
    max_results: int | None = Field(default=None, ge=1, le=100)
    persist: bool = Field(default=True, description="Persist EntityMatch rows and emit Evidence.")

    @model_validator(mode="after")
    def _exactly_one_subject_source(self) -> "ResolveSubjectRequest":
        provided = [x for x in (self.subject, self.client_id, self.ownership_entity_id) if x is not None]
        if len(provided) != 1:
            raise ValueError("Provide exactly one of: subject, client_id, ownership_entity_id.")
        return self


class ResolveBatchRequest(BaseModel):
    subjects: list[ResolveSubjectRequest] = Field(min_length=1, max_length=50)


class ResolveBatchResponse(BaseModel):
    runs: list[ResolutionRunResult]
    total_subjects: int
    total_results: int


class EntityMatchRead(ORMReadModel):
    id: int
    subject_type: EntityMatchSubjectType
    subject_id: int
    subject_ref: str | None
    candidate_sanctions_entity_id: int | None
    candidate_provider: str | None
    candidate_external_id: str | None
    candidate_name: str
    candidate_source_tier: str | None
    name_similarity_score: float
    corroboration_score: float | None
    combined_confidence: float
    matched_attributes: str | None
    conflicting_attributes: str | None
    reasons: str | None
    status: EntityMatchStatus
    resolved_at: datetime


class EvidenceRead(ORMReadModel):
    id: int
    client_id: int | None
    entity_match_id: int | None
    evidence_type: EvidenceType
    extracted_fact: str
    snippet: str | None
    structured_facts: str | None
    confidence: float
    producing_component: str
    source_dataset: str
    source_tier: SourceTier
    provider_name: str | None
    external_record_id: str | None
    source_reference: str | None
    retrieved_at: datetime | None
    created_at: datetime


class EvidenceListResponse(BaseModel):
    evidence: list[EvidenceRead]
    total: int


__all__ = [
    "ResolvePairRequest",
    "ResolveSubjectRequest",
    "ResolveBatchRequest",
    "ResolveBatchResponse",
    "EntityMatchRead",
    "EvidenceRead",
    "EvidenceListResponse",
    "EntityResolutionResult",
    "ResolutionRunResult",
]
