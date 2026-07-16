"""
Adapters: every entity shape in the system -> `ResolutionSubject`.

This module is the *only* place that knows what a Client, a SanctionsEntity,
an OwnershipEntity, or an ExternalEntityCandidate looks like. Everything
downstream (scorers, confidence, pipeline) sees only ResolutionSubject. That
containment is what makes the engine generic: adding a new entity source is
one adapter here and nothing else.

Note what is deliberately NOT populated for a Client: no DOB, no identifiers,
no nationality. The Phase 0 client master genuinely has none of those fields
(docs/data-dictionary.md) -- inventing them would fabricate corroboration.
The consequence is honest and important: a Client screened against the
authoritative lists can rarely exceed CANDIDATE, because there is nothing to
corroborate a name hit with. docs/phase-0-dataset-audit.md SS3 predicted
exactly this, and the engine reproduces it rather than hiding it.
"""

from __future__ import annotations

from app.models.client import Client
from app.models.ownership import OwnershipEntity
from app.models.sanctions import SanctionsEntity
from app.providers.schemas import ExternalEntityCandidate
from app.resolution.schemas import ResolutionSubject


def client_to_subject(client: Client) -> ResolutionSubject:
    return ResolutionSubject(
        subject_ref=f"client:{client.external_client_id}",
        internal_id=client.id,
        name=client.client_name,
        aliases=[],
        # `client_type` (NGO / Financial Institution / Corporate / Individual) is a
        # KYC classification, but "Individual" does map cleanly onto the person/
        # organization axis the entity_type scorer compares on.
        entity_type=client.client_type.value if client.client_type else None,
        countries=[client.country] if client.country else [],
        nationalities=[],
        dates_of_birth=[],
        identifiers=[],
        organizations=[],
        related_entity_refs=[],
        provider="internal_kyc",
        source_tier=client.source_tier.value if client.source_tier else None,
    )


def sanctions_entity_to_subject(entity: SanctionsEntity) -> ResolutionSubject:
    aliases = [a.alias_name for a in (entity.aliases or []) if a.alias_name]
    dobs = [entity.birth_date.isoformat()] if entity.birth_date else []
    return ResolutionSubject(
        subject_ref=f"sanctions:{entity.source_type.value}:{entity.external_entity_id}",
        internal_id=entity.id,
        name=entity.name,
        aliases=aliases,
        entity_type=entity.entity_type,
        countries=[entity.country] if entity.country else [],
        # The Phase 0 sources conflate country-of-record and nationality; rather
        # than guess which one a given row means, `country` is populated and
        # nationality is left empty for DB-sourced sanctions rows. The
        # nationality scorer then reports not-applicable instead of risking a
        # false conflict on a field the data doesn't actually distinguish.
        nationalities=[],
        dates_of_birth=dobs,
        identifiers=[],
        organizations=[],
        related_entity_refs=[],
        provider=entity.source_type.value,
        source_tier=entity.source_tier.value if entity.source_tier else None,
    )


def ownership_entity_to_subject(
    entity: OwnershipEntity, related_refs: list[str] | None = None
) -> ResolutionSubject:
    return ResolutionSubject(
        subject_ref=f"ownership:{entity.graph_key}:{entity.external_entity_id}",
        internal_id=entity.id,
        name=entity.name,
        aliases=[],
        entity_type=entity.entity_type,
        countries=[],
        nationalities=[entity.nationality] if entity.nationality else [],
        dates_of_birth=[entity.dob] if entity.dob else [],
        identifiers=[],
        organizations=[],
        related_entity_refs=related_refs or [],
        provider="ubo_graph_fixture",
        source_tier=entity.source_tier.value if entity.source_tier else None,
    )


def external_candidate_to_subject(candidate: ExternalEntityCandidate) -> ResolutionSubject:
    """Provider results already arrive normalized (Phase 1's
    ExternalEntityCandidate contract), so this is close to a field rename --
    which is exactly the payoff of having normalized at the provider boundary."""
    return ResolutionSubject(
        subject_ref=f"provider:{candidate.provider}:{candidate.external_id}",
        name=candidate.name,
        aliases=list(candidate.aliases),
        entity_type=candidate.entity_type,
        countries=list(candidate.countries),
        nationalities=list(candidate.nationalities),
        dates_of_birth=list(candidate.dates_of_birth),
        identifiers=list(candidate.identifiers),
        organizations=[],
        related_entity_refs=[],
        provider=candidate.provider,
        source_tier=candidate.source_tier.value if candidate.source_tier else None,
    )
