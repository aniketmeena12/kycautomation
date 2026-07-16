"""
Entity-resolution contracts.

`ResolutionSubject` is the single generic shape every entity is reduced to
before matching -- a Client, an OwnershipEntity, a SanctionsEntity, or a
provider-returned ExternalEntityCandidate all become one of these. Nothing
downstream of `app/resolution/adapters.py` knows or cares which it was.
This is what makes the pipeline entity-agnostic: no scorer, no confidence
rule, and no pipeline stage can branch on "is this a client" or on any
specific entity's name.

Every scorer returns a `ScorerResult` -- a score, a human-readable reason,
and a signed confidence impact. Never a bare boolean (Phase 3 brief SS3):
a boolean would throw away exactly the information the explainability and
confidence layers need.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import EntityMatchStatus


class ResolutionSubject(BaseModel):
    """A normalized, source-agnostic entity ready for matching.

    Every list field defaults empty rather than None: "we have no aliases"
    and "aliases not applicable" are the same thing for matching purposes,
    and a scorer that finds an empty list reports `applicable=False` rather
    than fabricating a zero score (see ScorerResult.applicable).
    """

    subject_ref: str = Field(
        description="Opaque caller-supplied reference, e.g. 'client:3' or 'ofac:001923'."
    )
    # Internal DB primary key, when this subject came from one of our own
    # tables. None for a streaming-provider candidate, which by design has no
    # local row (docs/phase-2-ingestion.md SS3). Carried here so the service
    # layer can set a real FK without re-querying -- see
    # app/services/entity_resolution_service.py.
    internal_id: int | None = None
    name: str
    aliases: list[str] = Field(default_factory=list)
    entity_type: str | None = None
    countries: list[str] = Field(default_factory=list)
    nationalities: list[str] = Field(default_factory=list)
    dates_of_birth: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    related_entity_refs: list[str] = Field(default_factory=list)

    # Provenance of where this subject came from, carried through so a result
    # can always cite its source (never merged/flattened -- ADR-002).
    provider: str | None = None
    source_tier: str | None = None


class ScorerResult(BaseModel):
    """One feature's verdict.

    `applicable=False` means the data needed for this comparison was absent
    on one or both sides -- crucially NOT the same as "compared and found
    different". A non-applicable scorer contributes nothing to confidence
    (weight is skipped, not applied as zero), because absence of evidence is
    not evidence of absence -- see docs/phase-0-dataset-audit.md SS6G, which
    called this out as a requirement before any code existed.
    """

    scorer: str
    applicable: bool
    score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="0..1 similarity; None when not applicable."
    )
    reason: str
    confidence_impact: float = Field(
        default=0.0, description="Signed points contributed to the 0-100 confidence."
    )
    is_conflict: bool = False


class ResolutionExplanation(BaseModel):
    """Human-readable breakdown. Nothing in a result may be opaque
    (Phase 3 brief SS12)."""

    overall_confidence: float
    status: EntityMatchStatus
    positive_factors: list[str] = Field(default_factory=list)
    negative_factors: list[str] = Field(default_factory=list)
    not_applicable_factors: list[str] = Field(default_factory=list)
    summary: str


class EntityResolutionResult(BaseModel):
    subject: ResolutionSubject
    candidate: ResolutionSubject
    confidence: float = Field(ge=0.0, le=100.0)
    status: EntityMatchStatus
    matched_attributes: list[str] = Field(default_factory=list)
    conflicting_attributes: list[str] = Field(default_factory=list)
    scorer_results: list[ScorerResult] = Field(default_factory=list)
    explanation: ResolutionExplanation
    provider: str | None = None
    resolved_at: datetime
    persisted_match_id: int | None = None


class ResolutionRunResult(BaseModel):
    """One subject resolved against N generated candidates."""

    subject: ResolutionSubject
    results: list[EntityResolutionResult] = Field(default_factory=list)
    candidates_considered: int = 0
    providers_queried: list[str] = Field(default_factory=list)
    provider_errors: list[str] = Field(default_factory=list)
    resolved_at: datetime
