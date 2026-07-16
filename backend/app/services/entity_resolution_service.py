"""
EntityResolutionService -- the write layer around the pure pipeline.

`app/resolution/pipeline.py` computes and never persists. This service runs
it, persists the outcome to `EntityMatch`, optionally emits `Evidence` for
matches worth recording, and writes an audit entry. Keeping the split means a
caller can score without committing (useful for a what-if, and for testing
the matching logic with no database at all).

Two rules enforced here rather than trusted:

  - The engine never persists CONFIRMED/HUMAN_REVIEWED. `_assert_machine_status`
    raises if the pipeline ever hands back a human-only state, so
    "Do not mark anything 'confirmed'" (Phase 3 brief SS9) is a runtime
    invariant, not a convention someone can quietly break later.
  - Rejected matches are persisted too, by default. A compliance system must
    be able to show *what it considered and dismissed*, not just what it
    surfaced -- that is the audit story for a false positive.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.core.enums import (
    ActorType,
    EntityMatchStatus,
    EntityMatchSubjectType,
    SourceTier,
)
from app.models.client import Client
from app.repositories.entity_match_repository import EntityMatchRepository
from app.resolution.adapters import client_to_subject, ownership_entity_to_subject
from app.resolution.pipeline import EntityResolutionPipeline
from app.resolution.schemas import EntityResolutionResult, ResolutionRunResult, ResolutionSubject
from app.services.audit_service import record_audit_event
from app.services.evidence_service import EvidenceService

MAX_JSON_FIELD_CHARS = 4000

HUMAN_ONLY_STATUSES = frozenset({EntityMatchStatus.CONFIRMED, EntityMatchStatus.HUMAN_REVIEWED})

# Statuses worth turning into an Evidence row. A rejected candidate is still
# persisted as an EntityMatch (see module docstring) but does not become
# evidence -- evidence is for facts that support a later risk decision, and
# "we looked and it wasn't him" is not one.
EVIDENCE_WORTHY_STATUSES = frozenset({EntityMatchStatus.HIGH_CONFIDENCE, EntityMatchStatus.POSSIBLE})


def _truncate_json(value) -> str | None:
    if value is None:
        return None
    encoded = json.dumps(value, default=str)
    if len(encoded) > MAX_JSON_FIELD_CHARS:
        return json.dumps({"truncated": True, "original_length": len(encoded)})
    return encoded


class EntityResolutionService:
    def __init__(
        self,
        db: Session,
        pipeline: EntityResolutionPipeline | None = None,
        evidence_service: EvidenceService | None = None,
    ) -> None:
        self._db = db
        self._pipeline = pipeline or EntityResolutionPipeline()
        self._matches = EntityMatchRepository(db)
        self._evidence = evidence_service or EvidenceService(db)

    @property
    def pipeline(self) -> EntityResolutionPipeline:
        return self._pipeline

    # ------------------------------------------------------------------ #
    # Subject construction (generic -- any entity, never a specific one)
    # ------------------------------------------------------------------ #

    def subject_for_client(self, client: Client) -> ResolutionSubject:
        return client_to_subject(client)

    def subject_for_ownership_entity(
        self, entity, related_refs: list[str] | None = None
    ) -> ResolutionSubject:
        return ownership_entity_to_subject(entity, related_refs)

    # ------------------------------------------------------------------ #
    # Resolve + persist
    # ------------------------------------------------------------------ #

    def resolve_and_persist(
        self,
        subject: ResolutionSubject,
        *,
        subject_type: EntityMatchSubjectType,
        subject_id: int,
        client_id: int | None = None,
        include_local_db: bool = True,
        include_providers: bool = False,
        allow_expensive_providers: bool = False,
        source_tier: SourceTier | None = None,
        min_confidence: float | None = None,
        max_results: int | None = None,
        persist_rejected: bool = True,
        emit_evidence: bool = True,
        correlation_id: str | None = None,
    ) -> ResolutionRunResult:
        run = self._pipeline.resolve(
            self._db,
            subject,
            include_local_db=include_local_db,
            include_providers=include_providers,
            allow_expensive_providers=allow_expensive_providers,
            source_tier=source_tier,
            min_confidence=min_confidence,
            max_results=max_results,
        )

        for result in run.results:
            self._assert_machine_status(result.status)
            if not persist_rejected and result.status == EntityMatchStatus.AUTO_REJECTED:
                continue

            match = self._persist_match(result, subject_type=subject_type, subject_id=subject_id)
            result.persisted_match_id = match.id

            if emit_evidence and result.status in EVIDENCE_WORTHY_STATUSES:
                self._evidence.record_sanctions_match_evidence(
                    result=result, entity_match_id=match.id, client_id=client_id
                )

        self._db.commit()

        record_audit_event(
            self._db,
            actor_type=ActorType.SYSTEM,
            actor_id="entity_resolution_service",
            action="entity_resolution_run",
            target_type=subject_type.value,
            target_id=str(subject_id),
            reason=f"Resolved '{subject.subject_ref}' against {run.candidates_considered} candidate(s).",
            new_value={
                "candidates_considered": run.candidates_considered,
                "results": len(run.results),
                "providers_queried": run.providers_queried,
                "statuses": [r.status.value for r in run.results],
            },
            correlation_id=correlation_id,
        )
        return run

    def _persist_match(
        self, result: EntityResolutionResult, *, subject_type: EntityMatchSubjectType, subject_id: int
    ):
        name_result = next((r for r in result.scorer_results if r.scorer == "name"), None)
        name_score = (name_result.score or 0.0) if name_result and name_result.applicable else 0.0

        corroborating = [
            r for r in result.scorer_results if r.applicable and r.scorer not in ("name", "exact_ref")
        ]
        corroboration = (
            sum((r.score or 0.0) for r in corroborating) / len(corroborating) if corroborating else None
        )

        # A DB-sourced candidate carries its own primary key through the
        # adapter, so the FK is set without a second query. A streaming-provider
        # candidate has no local row at all and correctly leaves it NULL --
        # it is identified by candidate_provider + candidate_external_id
        # instead (see app/models/resolution.py).
        sanctions_entity_id = (
            result.candidate.internal_id
            if (result.candidate.subject_ref or "").startswith("sanctions:")
            else None
        )

        match, _ = self._matches.upsert(
            subject_ref=result.subject.subject_ref,
            candidate_provider=result.candidate.provider,
            candidate_external_id=_external_id(result.candidate.subject_ref),
            subject_type=subject_type,
            subject_id=subject_id,
            candidate_sanctions_entity_id=sanctions_entity_id,
            candidate_name=result.candidate.name,
            candidate_source_tier=result.candidate.source_tier,
            name_similarity_score=name_score,
            corroboration_score=corroboration,
            combined_confidence=result.confidence,
            matched_attributes=_truncate_json(result.matched_attributes),
            conflicting_attributes=_truncate_json(result.conflicting_attributes),
            reasons=_truncate_json(
                {
                    "summary": result.explanation.summary,
                    "positive": result.explanation.positive_factors,
                    "negative": result.explanation.negative_factors,
                    "not_applicable": result.explanation.not_applicable_factors,
                }
            ),
            status=result.status,
            resolved_at=result.resolved_at,
        )
        return match

    @staticmethod
    def _assert_machine_status(status: EntityMatchStatus) -> None:
        if status in HUMAN_ONLY_STATUSES:
            raise ValueError(
                f"The resolution engine must never produce {status.value}; that state is reserved "
                "for a human reviewer in a later phase."
            )


def _external_id(subject_ref: str | None) -> str | None:
    """'sanctions:CURATED_OFAC:001923' -> '001923'; 'provider:tier1_ofac_lookup:36' -> '36'.

    Splits on the LAST colon only, so an external id that itself contains a
    colon (OpenSanctions ids like 'NK-223CQ...' don't today, but a future
    provider's might) survives intact.
    """
    if not subject_ref:
        return None
    _, separator, tail = subject_ref.rpartition(":")
    return tail if separator else None
