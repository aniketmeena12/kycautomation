"""
EvidenceService -- the single writer for Evidence rows.

Every evidence type the Phase 3 brief (SS10) calls for is a method here:
sanctions hit, news article, transaction, ownership graph, provider response,
manual. They all funnel through `_create`, so provenance, size bounds, and
the structured/prose split are enforced in exactly one place rather than
trusted to each caller.

Every Evidence row carries (brief SS10):
  type            -> evidence_type
  source          -> source_dataset / provider_name
  provenance      -> source_tier / source_type / provider_kind / ingested_at
  timestamp       -> created_at (+ retrieved_at for provider-sourced)
  summary         -> extracted_fact (prose, human-readable)
  structured facts-> structured_facts (JSON, machine-readable)
  linked entity   -> entity_match_id and/or source_record_type/id
  linked client   -> client_id
  confidence      -> confidence

THE EVIDENCE GRAPH (brief SS11) is these FKs, not a separate structure:

    Client --client_id--> Evidence --entity_match_id--> EntityMatch
                              |                              |
                              |                    candidate_sanctions_entity_id
                              |                    or candidate_provider/external_id
                              +-- source_dataset / provider_name --> Source

Multiple Evidence rows per entity is the normal case, not an edge case -- the
FKs are many-to-one in that direction by design.

Note `confidence` is copied from the resolution result rather than recomputed:
the confidence engine is the single authority on that number (see
app/resolution/confidence.py). Two components computing confidence
independently is how they drift apart.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.enums import EvidenceType, ProviderKind, SourceTier, SourceType
from app.models.evidence import Evidence
from app.repositories.evidence_repository import EvidenceRepository
from app.resolution.schemas import EntityResolutionResult

MAX_STRUCTURED_FACTS_CHARS = 4000
MAX_SNIPPET_CHARS = 1000

PRODUCING_COMPONENT = "entity_resolution_service"


def _encode_facts(facts: dict | None) -> str | None:
    """Bounded JSON. Mirrors the audit service's truncation discipline
    (app/services/audit_service.py): an Evidence row must never become a dump
    of an entire source record."""
    if facts is None:
        return None
    encoded = json.dumps(facts, default=str)
    if len(encoded) > MAX_STRUCTURED_FACTS_CHARS:
        return json.dumps(
            {"truncated": True, "original_length": len(encoded), "keys": sorted(facts.keys())}, default=str
        )
    return encoded


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "..."


class EvidenceService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = EvidenceRepository(db)

    # ------------------------------------------------------------------ #
    # Generic creator -- all typed helpers below funnel through this.
    # ------------------------------------------------------------------ #
    def _create(
        self,
        *,
        evidence_type: EvidenceType,
        extracted_fact: str,
        confidence: float,
        source_dataset: str,
        source_tier: SourceTier,
        source_type: SourceType,
        producing_component: str = PRODUCING_COMPONENT,
        client_id: int | None = None,
        entity_match_id: int | None = None,
        source_record_type: str | None = None,
        source_record_id: int | None = None,
        structured_facts: dict | None = None,
        snippet: str | None = None,
        provider_name: str | None = None,
        provider_kind: ProviderKind | None = None,
        external_record_id: str | None = None,
        source_reference: str | None = None,
        retrieved_at=None,
        query_context: dict | None = None,
    ) -> Evidence:
        return self._repo.create(
            evidence_type=evidence_type,
            extracted_fact=extracted_fact,
            confidence=confidence,
            source_dataset=source_dataset,
            source_tier=source_tier,
            source_type=source_type,
            producing_component=producing_component,
            client_id=client_id,
            entity_match_id=entity_match_id,
            source_record_type=source_record_type,
            source_record_id=source_record_id,
            structured_facts=_encode_facts(structured_facts),
            snippet=_clip(snippet, MAX_SNIPPET_CHARS),
            provider_name=provider_name,
            provider_kind=provider_kind,
            external_record_id=external_record_id,
            source_reference=source_reference,
            retrieved_at=retrieved_at,
            query_context=_encode_facts(query_context),
        )

    # ------------------------------------------------------------------ #
    # Typed evidence (Phase 3 brief SS10)
    # ------------------------------------------------------------------ #

    def record_sanctions_match_evidence(
        self, *, result: EntityResolutionResult, entity_match_id: int, client_id: int | None = None
    ) -> Evidence:
        """A resolution outcome recorded as evidence.

        The tier is copied from the candidate, never assumed: evidence derived
        from the Tier-2 curated fixture must stay visibly Tier-2 and never be
        presentable as an authoritative hit (ADR-002).
        """
        candidate = result.candidate
        tier = _parse_tier(candidate.source_tier)

        return self._create(
            evidence_type=EvidenceType.SANCTIONS_MATCH,
            extracted_fact=(
                f"'{result.subject.name}' resolved against '{candidate.name}' "
                f"with {result.confidence:.0f}/100 confidence ({result.status.value})."
            ),
            confidence=result.confidence / 100.0,
            source_dataset=candidate.provider or "unknown",
            source_tier=tier,
            source_type=(
                SourceType.INTERNAL_KYC if tier == SourceTier.INTERNAL else _tier_to_source_type(tier)
            ),
            client_id=client_id,
            entity_match_id=entity_match_id,
            source_record_type="EntityMatch",
            source_record_id=entity_match_id,
            structured_facts={
                "subject_ref": result.subject.subject_ref,
                "candidate_ref": candidate.subject_ref,
                "candidate_name": candidate.name,
                "confidence": result.confidence,
                "status": result.status.value,
                "matched_attributes": result.matched_attributes,
                "conflicting_attributes": result.conflicting_attributes,
                "scores": {
                    r.scorer: r.score for r in result.scorer_results if r.applicable and r.score is not None
                },
            },
            snippet=result.explanation.summary,
            provider_name=candidate.provider,
            external_record_id=_external_ref(candidate.subject_ref),
            retrieved_at=result.resolved_at,
        )

    def record_adverse_media_evidence(
        self,
        *,
        article_external_id: str,
        summary: str,
        confidence: float,
        provider_name: str,
        provider_kind: ProviderKind,
        source_tier: SourceTier,
        client_id: int | None = None,
        entity_match_id: int | None = None,
        snippet: str | None = None,
        structured_facts: dict | None = None,
        source_reference: str | None = None,
        retrieved_at=None,
    ) -> Evidence:
        """A news/article hit.

        `snippet` is article text -- untrusted content stored verbatim and
        never executed or interpreted (docs/phase-1-foundation.md's
        "DATA IS DATA, NOT INSTRUCTIONS"). Phase 3 does no NLP on it; a future
        agent must treat it as data.
        """
        return self._create(
            evidence_type=EvidenceType.ADVERSE_MEDIA,
            extracted_fact=summary,
            confidence=confidence,
            source_dataset=provider_name,
            source_tier=source_tier,
            source_type=SourceType.ADVERSE_MEDIA_FIXTURE,
            client_id=client_id,
            entity_match_id=entity_match_id,
            source_record_type="AdverseMediaArticle",
            structured_facts=structured_facts,
            snippet=snippet,
            provider_name=provider_name,
            provider_kind=provider_kind,
            external_record_id=article_external_id,
            source_reference=source_reference,
            retrieved_at=retrieved_at,
        )

    def record_transaction_evidence(
        self,
        *,
        client_id: int,
        summary: str,
        confidence: float,
        structured_facts: dict,
        source_dataset: str,
        transaction_id: int | None = None,
    ) -> Evidence:
        return self._create(
            evidence_type=EvidenceType.TRANSACTION_TYPOLOGY,
            extracted_fact=summary,
            confidence=confidence,
            source_dataset=source_dataset,
            source_tier=SourceTier.INTERNAL,
            source_type=SourceType.INTERNAL_KYC,
            client_id=client_id,
            source_record_type="Transaction",
            source_record_id=transaction_id,
            structured_facts=structured_facts,
        )

    def record_ownership_evidence(
        self,
        *,
        summary: str,
        confidence: float,
        structured_facts: dict,
        ownership_entity_id: int | None = None,
        client_id: int | None = None,
        entity_match_id: int | None = None,
    ) -> Evidence:
        return self._create(
            evidence_type=EvidenceType.UBO_EXPOSURE,
            extracted_fact=summary,
            confidence=confidence,
            source_dataset="ubo_graph_fixture",
            source_tier=SourceTier.TIER_2_CURATED_DEMO,
            source_type=SourceType.UBO_GRAPH_FIXTURE,
            client_id=client_id,
            entity_match_id=entity_match_id,
            source_record_type="OwnershipEntity",
            source_record_id=ownership_entity_id,
            structured_facts=structured_facts,
        )

    def record_provider_response_evidence(
        self,
        *,
        provider_name: str,
        provider_kind: ProviderKind,
        source_tier: SourceTier,
        summary: str,
        confidence: float,
        structured_facts: dict | None = None,
        client_id: int | None = None,
        query_context: dict | None = None,
        retrieved_at=None,
    ) -> Evidence:
        """A provider response recorded as evidence in its own right --
        including a NO_RESULTS/unavailable one. 'We queried X and it found
        nothing' is an auditable fact about the investigation's coverage."""
        return self._create(
            evidence_type=EvidenceType.PROVIDER_RESPONSE,
            extracted_fact=summary,
            confidence=confidence,
            source_dataset=provider_name,
            source_tier=source_tier,
            source_type=_tier_to_source_type(source_tier),
            client_id=client_id,
            structured_facts=structured_facts,
            provider_name=provider_name,
            provider_kind=provider_kind,
            query_context=query_context,
            retrieved_at=retrieved_at,
        )

    def record_manual_evidence(
        self,
        *,
        author: str,
        summary: str,
        confidence: float,
        client_id: int | None = None,
        entity_match_id: int | None = None,
        structured_facts: dict | None = None,
    ) -> Evidence:
        """Human-entered evidence. `producing_component` records the author so
        a manual note is never mistaken for a machine-derived fact."""
        return self._create(
            evidence_type=EvidenceType.MANUAL,
            extracted_fact=summary,
            confidence=confidence,
            source_dataset="manual_entry",
            source_tier=SourceTier.INTERNAL,
            source_type=SourceType.INTERNAL_KYC,
            producing_component=f"human:{author}",
            client_id=client_id,
            entity_match_id=entity_match_id,
            structured_facts=structured_facts,
        )

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #
    def list_for_client(self, client_id: int) -> list[Evidence]:
        return self._repo.list_for_client(client_id)

    def list_for_entity_match(self, entity_match_id: int) -> list[Evidence]:
        return self._repo.list_for_entity_match(entity_match_id)


def _parse_tier(value: str | None) -> SourceTier:
    if not value:
        return SourceTier.INTERNAL
    try:
        return SourceTier(value)
    except ValueError:
        return SourceTier.INTERNAL


def _tier_to_source_type(tier: SourceTier) -> SourceType:
    if tier == SourceTier.TIER_2_CURATED_DEMO:
        return SourceType.CURATED_OFAC
    if tier == SourceTier.TIER_1_AUTHORITATIVE:
        return SourceType.OFAC_SDN
    return SourceType.INTERNAL_KYC


def _external_ref(subject_ref: str | None) -> str | None:
    if not subject_ref:
        return None
    _, separator, tail = subject_ref.rpartition(":")
    return tail if separator else None
