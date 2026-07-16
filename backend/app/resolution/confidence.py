"""
Deterministic confidence engine.

This is the only place a 0-100 confidence number is produced, and it is pure
arithmetic over scorer outputs and externally-loaded weights -- no ML, no
LLM, no heuristics hidden in a scorer. That separation is the project's core
design principle applied one level down: scorers *observe*, this engine
*decides*, and it does so reproducibly. Same inputs + same config always give
the same number.

--------------------------------------------------------------------------
THE FORMULA
--------------------------------------------------------------------------
    earned    = SUM(score_i * positive_weight_i)   over APPLICABLE scorers
    possible  = SUM(positive_weight_i)             over APPLICABLE scorers
    base      = (earned / possible) * 100
    penalty   = SUM(conflict_penalty_i)            over CONFLICTING scorers
    final     = clamp(base - penalty, 0, 100)

Two deliberate properties:

1. `possible` counts only APPLICABLE scorers. An entity pair that simply
    lacks DOB data is not punished for it -- the denominator shrinks instead.
    Dividing by the full weight total would make every match against
    sparse Tier-1 OFAC rows (most have empty Remarks -- docs/data-dictionary.md)
    look weak regardless of how well the available attributes agree.

2. A conflict is penalized TWICE, and that is intentional: it earns ~0 of its
    positive weight AND subtracts a penalty. A contradiction is not merely
    "no evidence for" -- it is evidence against, and in a compliance context
    it should be able to sink an otherwise-perfect name match. This is what
    makes `AL-RASHID TRUST` (name 77/100, but an organization where a person
    was sought) resolve to REJECTED rather than to a plausible-looking hit.

--------------------------------------------------------------------------
THE NAME FLOOR
--------------------------------------------------------------------------
A pair whose name similarity falls below `thresholds.name_floor` is rejected
outright, whatever the other attributes say. Two entities sharing a country
and an entity type are not a match just because those agree -- without a name
match there is no candidate identity to corroborate in the first place.
"""

from __future__ import annotations

from app.core.enums import EntityMatchStatus
from app.resolution.config import ResolutionWeights, get_weights
from app.resolution.schemas import ResolutionExplanation, ScorerResult


class ConfidenceBreakdown:
    """Intermediate arithmetic, exposed so tests and the explainability layer
    can assert on the exact numbers rather than re-deriving them."""

    def __init__(self) -> None:
        self.earned: float = 0.0
        self.possible: float = 0.0
        self.penalty: float = 0.0
        self.base: float = 0.0
        self.final: float = 0.0
        self.matched_attributes: list[str] = []
        self.conflicting_attributes: list[str] = []
        self.not_applicable: list[str] = []
        self.name_floor_triggered: bool = False


class ConfidenceEngine:
    def __init__(self, weights: ResolutionWeights | None = None) -> None:
        self._weights = weights or get_weights()

    @property
    def weights(self) -> ResolutionWeights:
        return self._weights

    def compute(self, scorer_results: list[ScorerResult]) -> ConfidenceBreakdown:
        w = self._weights
        breakdown = ConfidenceBreakdown()

        for result in scorer_results:
            weight = w.positive_weight(result.scorer)

            if not result.applicable:
                breakdown.not_applicable.append(result.scorer)
                result.confidence_impact = 0.0
                continue

            breakdown.possible += weight
            gained = (result.score or 0.0) * weight
            breakdown.earned += gained

            if result.is_conflict:
                penalty = w.conflict_penalty(result.scorer)
                breakdown.penalty += penalty
                breakdown.conflicting_attributes.append(result.scorer)
                # Impact is reported in final 0-100 points: what it failed to
                # earn is captured by `base`; what it actively cost is the penalty.
                result.confidence_impact = -penalty
            else:
                result.confidence_impact = gained
                if self._counts_as_matched(result):
                    breakdown.matched_attributes.append(result.scorer)

        breakdown.base = (breakdown.earned / breakdown.possible * 100.0) if breakdown.possible > 0 else 0.0
        breakdown.final = max(0.0, min(100.0, breakdown.base - breakdown.penalty))

        name_result = next((r for r in scorer_results if r.scorer == "name"), None)
        if name_result is not None and name_result.applicable:
            if (name_result.score or 0.0) < w.thresholds.name_floor:
                breakdown.name_floor_triggered = True

        return breakdown

    def _counts_as_matched(self, result: ScorerResult) -> bool:
        """A scorer is reported as a 'matched attribute' only when it actually
        agreed, not merely when it was applicable. Set-overlap scorers are
        binary (1.0 or conflict); fuzzy ones must clear their configured
        threshold."""
        score = result.score or 0.0
        thresholds = self._weights.scorer_thresholds
        if result.scorer == "name":
            return score >= thresholds.name_match
        if result.scorer == "alias":
            return score >= thresholds.alias_match
        if result.scorer == "organization":
            return score >= thresholds.organization_match
        return score > 0.0

    def status_for(self, breakdown: ConfidenceBreakdown) -> EntityMatchStatus:
        """Map a confidence to a state.

        The engine can reach CANDIDATE / POSSIBLE / HIGH_CONFIDENCE /
        AUTO_REJECTED and nothing else. It NEVER returns CONFIRMED or
        HUMAN_REVIEWED -- those are reserved for a human acting in a later
        phase (Phase 3 brief SS9: "Do not mark anything 'confirmed'").
        Enforced by tests.
        """
        t = self._weights.thresholds
        if breakdown.name_floor_triggered:
            return EntityMatchStatus.AUTO_REJECTED
        if breakdown.final < t.rejected_below:
            return EntityMatchStatus.AUTO_REJECTED
        if breakdown.final >= t.high_confidence:
            return EntityMatchStatus.HIGH_CONFIDENCE
        if breakdown.final >= t.possible:
            return EntityMatchStatus.POSSIBLE
        return EntityMatchStatus.CANDIDATE

    def explain(
        self, breakdown: ConfidenceBreakdown, status: EntityMatchStatus, scorer_results: list[ScorerResult]
    ) -> ResolutionExplanation:
        positives: list[str] = []
        negatives: list[str] = []
        not_applicable: list[str] = []

        for result in scorer_results:
            if not result.applicable:
                not_applicable.append(f"{result.scorer}: {result.reason}")
            elif result.is_conflict:
                negatives.append(f"{result.reason} (-{abs(result.confidence_impact):.0f} pts)")
            else:
                positives.append(f"{result.reason} (+{result.confidence_impact:.0f} pts)")

        if breakdown.name_floor_triggered:
            negatives.insert(
                0,
                (
                    f"Name similarity below the {self._weights.thresholds.name_floor * 100:.0f}/100 floor -- "
                    "rejected regardless of other attributes."
                ),
            )

        summary = (
            f"Confidence {breakdown.final:.0f}/100 -> {status.value}. "
            f"Earned {breakdown.earned:.0f} of {breakdown.possible:.0f} applicable weight "
            f"(base {breakdown.base:.0f}), penalties -{breakdown.penalty:.0f}. "
            f"Matched: {breakdown.matched_attributes or 'none'}. "
            f"Conflicts: {breakdown.conflicting_attributes or 'none'}. "
            f"Not comparable: {breakdown.not_applicable or 'none'}."
        )

        return ResolutionExplanation(
            overall_confidence=round(breakdown.final, 2),
            status=status,
            positive_factors=positives,
            negative_factors=negatives,
            not_applicable_factors=not_applicable,
            summary=summary,
        )
