"""
The entity-resolution pipeline.

Stages (Phase 3 brief SS2), each independently testable and independently
replaceable:

    1. Candidate Generation   -> app/resolution/candidates.py
    2. Exact Matching         -> `_exact_match_ref` (short-circuit, below)
    3. Fuzzy Matching         -> app/resolution/scorers/name.py
    4. Attribute Matching     -> app/resolution/scorers/attributes.py
    5. Context Matching       -> ownership/organization scorers
    6. Confidence Calculation -> app/resolution/confidence.py
    7. Explainability         -> ConfidenceEngine.explain

`resolve_pair` (stages 3-7) is deliberately usable on its own, with no
database and no providers -- that is what makes the matching logic testable
in isolation and reusable by any caller that already has two entities.

The pipeline is pure computation: it never writes. Persisting an
EntityResolutionResult is the service layer's job
(app/services/entity_resolution_service.py), so a caller can score without
committing anything.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.enums import SourceTier
from app.resolution.candidates import CandidateGenerator
from app.resolution.confidence import ConfidenceEngine
from app.resolution.schemas import (
    EntityResolutionResult,
    ResolutionRunResult,
    ResolutionSubject,
    ScorerResult,
)
from app.resolution.scorers import Scorer, build_default_scorers


class EntityResolutionPipeline:
    def __init__(
        self,
        scorers: list[Scorer] | None = None,
        confidence_engine: ConfidenceEngine | None = None,
    ) -> None:
        self._scorers = scorers or build_default_scorers()
        self._confidence = confidence_engine or ConfidenceEngine()

    @property
    def confidence_engine(self) -> ConfidenceEngine:
        return self._confidence

    def resolve_pair(
        self, subject: ResolutionSubject, candidate: ResolutionSubject
    ) -> EntityResolutionResult:
        """Stages 2-7 for one pair. No I/O."""
        scorer_results: list[ScorerResult] = [s.score(subject, candidate) for s in self._scorers]

        exact_reason = self._exact_match_ref(subject, candidate)
        if exact_reason is not None:
            scorer_results.insert(
                0,
                ScorerResult(
                    scorer="exact_ref",
                    applicable=True,
                    score=1.0,
                    reason=exact_reason,
                ),
            )

        breakdown = self._confidence.compute(scorer_results)
        status = self._confidence.status_for(breakdown)
        explanation = self._confidence.explain(breakdown, status, scorer_results)

        return EntityResolutionResult(
            subject=subject,
            candidate=candidate,
            confidence=round(breakdown.final, 2),
            status=status,
            matched_attributes=breakdown.matched_attributes,
            conflicting_attributes=breakdown.conflicting_attributes,
            scorer_results=scorer_results,
            explanation=explanation,
            provider=candidate.provider,
            resolved_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _exact_match_ref(subject: ResolutionSubject, candidate: ResolutionSubject) -> str | None:
        """Stage 2 -- exact matching on the opaque subject_ref.

        Only fires when both sides carry the *same* fully-qualified ref (same
        provider AND same external id), i.e. genuinely the same record. It
        carries no configured weight and cannot by itself lift a pair over a
        threshold -- it is recorded for explainability, so a self-match is
        visibly a self-match rather than a suspiciously perfect fuzzy score.

        Deliberately NOT an exact-name match: two different people sharing a
        name string is precisely the false positive this system exists to
        catch, so "names are identical" must go through the same corroboration
        path as everything else.
        """
        if subject.subject_ref and subject.subject_ref == candidate.subject_ref:
            return f"Exact reference match on '{subject.subject_ref}' (same source record)."
        return None

    def resolve(
        self,
        db: Session,
        subject: ResolutionSubject,
        *,
        include_local_db: bool = True,
        include_providers: bool = False,
        allow_expensive_providers: bool = False,
        source_tier: SourceTier | None = None,
        min_confidence: float | None = None,
        max_results: int | None = None,
        generator: CandidateGenerator | None = None,
    ) -> ResolutionRunResult:
        """Full pipeline: generate candidates for `subject`, score each, and
        return them ranked by confidence."""
        generator = generator or CandidateGenerator(db)
        batch = generator.generate(
            subject,
            include_local_db=include_local_db,
            include_providers=include_providers,
            allow_expensive_providers=allow_expensive_providers,
            source_tier=source_tier,
        )

        results = [self.resolve_pair(subject, candidate) for candidate in batch.candidates]
        if min_confidence is not None:
            results = [r for r in results if r.confidence >= min_confidence]
        results.sort(key=lambda r: r.confidence, reverse=True)
        if max_results is not None:
            results = results[:max_results]

        return ResolutionRunResult(
            subject=subject,
            results=results,
            candidates_considered=len(batch.candidates),
            providers_queried=batch.providers_queried,
            provider_errors=batch.provider_errors,
            resolved_at=datetime.now(timezone.utc),
        )
