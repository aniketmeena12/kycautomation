"""
The deterministic, explainable risk engine.

This is the only component permitted to produce an authoritative numerical
risk score. It is pure arithmetic over RiskSignals and the config-loaded
Risk Factor Registry: no ML, no heuristics, and -- structurally -- no LLM.
There is no code path from a model output to this number; the engine's only
inputs are `RiskSignal` objects and a JSON registry. That is the project's
core design principle enforced by construction rather than by policy.

--------------------------------------------------------------------------
FORMULA (per factor, formula name itself comes from config)
--------------------------------------------------------------------------
    weight_x_confidence:  raw = weight * signal.confidence * confidence_multiplier
    weight_only:          raw = weight * confidence_multiplier

    contribution = min(raw, factor.max_contribution)
    score        = min(SUM(contributions), scoring.max_total_score)

Matches the Phase 4 brief's worked example exactly: weight 50 x confidence
0.82 -> contribution 41.

--------------------------------------------------------------------------
DESIGN NOTES
--------------------------------------------------------------------------
* Contributions are ADDITIVE and capped, not normalized. Unlike the entity-
  resolution engine (which divides by applicable weight because it answers
  "how well do these two records agree?"), risk answers "how much risk has
  accumulated?" -- and a client with one confirmed sanctions hit and no other
  data must not score higher than the same client with additional benign
  attributes, which is exactly what normalizing would do.

* Highest contribution per factor wins when several signals match the same
  factor. Two adverse-media articles are not twice the risk of one; they are
  one adverse-media finding with corroboration. Repetition is surfaced by the
  ALERT engine (`REPEATED_SIGNAL`), not by inflating the score.

* A signal matching no enabled factor is reported in `unmatched_signals`
  rather than silently dropped -- a registry gap should be visible, not
  invisible.

* Pure: no I/O, no DB, nothing persisted. The monitoring service persists.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.enums import RiskBand
from app.risk.config import RiskFactor, RiskRegistry, get_risk_registry
from app.risk.schemas import FactorContribution, RiskScoreResult, RiskSignal


class RiskEngine:
    def __init__(self, registry: RiskRegistry | None = None) -> None:
        self._registry = registry or get_risk_registry()

    @property
    def registry(self) -> RiskRegistry:
        return self._registry

    def score(
        self,
        client_id: int,
        signals: list[RiskSignal],
        *,
        previous_score: float | None = None,
    ) -> RiskScoreResult:
        best_by_factor: dict[str, tuple[RiskFactor, RiskSignal, float]] = {}
        matched_signal_keys: set[str] = set()

        for signal in signals:
            for factor in self._registry.enabled_factors():
                if not factor.matches(signal):
                    continue
                matched_signal_keys.add(signal.dedup_key)
                raw = self._raw_contribution(factor, signal)
                existing = best_by_factor.get(factor.id)
                if existing is None or raw > existing[2]:
                    best_by_factor[factor.id] = (factor, signal, raw)

        contributions = [
            self._build_contribution(factor, signal, raw) for factor, signal, raw in best_by_factor.values()
        ]
        # Deterministic ordering: biggest contributor first, then factor id to
        # break ties. Stable output matters for auditability and diffing.
        contributions.sort(key=lambda c: (-c.contribution, c.factor_id))

        total_before_cap = sum(c.contribution for c in contributions)
        max_total = self._registry.scoring.max_total_score
        score = min(total_before_cap, max_total)
        band = self._registry.band_for(score)

        unmatched = [s.dedup_key for s in signals if s.dedup_key not in matched_signal_keys]

        previous_band = self._registry.band_for(previous_score) if previous_score is not None else None
        delta = (score - previous_score) if previous_score is not None else None

        return RiskScoreResult(
            client_id=client_id,
            score=round(score, 2),
            band=band,
            previous_score=previous_score,
            previous_band=previous_band,
            delta=round(delta, 2) if delta is not None else None,
            contributions=contributions,
            unmatched_signals=unmatched,
            total_before_cap=round(total_before_cap, 2),
            capped_at_max=total_before_cap > max_total,
            scoring_logic_version=self._registry.scoring.scoring_logic_version,
            computed_at=datetime.now(timezone.utc),
            explanation=self._explain(
                score, band, contributions, total_before_cap, max_total, unmatched, delta
            ),
        )

    def _raw_contribution(self, factor: RiskFactor, signal: RiskSignal) -> float:
        formula = self._registry.scoring.contribution_formula
        if formula == "weight_only":
            return factor.weight * factor.confidence_multiplier
        # default: weight_x_confidence
        return factor.weight * signal.confidence * factor.confidence_multiplier

    def _build_contribution(self, factor: RiskFactor, signal: RiskSignal, raw: float) -> FactorContribution:
        capped_value = min(raw, factor.max_contribution)
        was_capped = raw > factor.max_contribution
        formula = self._registry.scoring.contribution_formula

        if formula == "weight_only":
            math = f"{factor.weight:g} x {factor.confidence_multiplier:g} (confidence ignored by config)"
        else:
            math = f"{factor.weight:g} x {signal.confidence:.2f} confidence" + (
                f" x {factor.confidence_multiplier:g} multiplier"
                if factor.confidence_multiplier != 1.0
                else ""
            )

        reason = f"{factor.name}: {math} = {capped_value:.1f} pts"
        if was_capped:
            reason += f" (raw {raw:.1f} capped at {factor.max_contribution:g})"
        reason += f". {signal.summary}"

        return FactorContribution(
            factor_id=factor.id,
            factor_name=factor.name,
            category=factor.category,
            severity=factor.severity,
            weight=factor.weight,
            signal_confidence=signal.confidence,
            confidence_multiplier=factor.confidence_multiplier,
            raw_contribution=round(raw, 2),
            contribution=round(capped_value, 2),
            capped=was_capped,
            reason=reason,
            signal_source=signal.source,
            evidence_ids=list(signal.evidence_ids),
            event_type=factor.event_type,
        )

    def _explain(
        self,
        score: float,
        band: RiskBand,
        contributions: list[FactorContribution],
        total_before_cap: float,
        max_total: float,
        unmatched: list[str],
        delta: float | None,
    ) -> str:
        if not contributions:
            return (
                f"Risk {score:.0f}/100 -> {band.value}. No risk factors matched any collected signal; "
                "nothing observed raises this client's risk."
            )

        parts = [f"Risk {score:.0f}/100 -> {band.value}."]
        if delta is not None:
            direction = "unchanged" if abs(delta) < 0.005 else ("up" if delta > 0 else "down")
            parts.append(f"Score {direction} by {abs(delta):.0f} pts since the previous snapshot.")

        top = ", ".join(f"{c.factor_name} +{c.contribution:.0f}" for c in contributions[:4])
        parts.append(f"Driven by: {top}.")

        if total_before_cap > max_total:
            parts.append(f"Contributions totalled {total_before_cap:.0f}, capped at {max_total:.0f}.")
        if unmatched:
            parts.append(f"{len(unmatched)} signal(s) matched no configured factor.")
        return " ".join(parts)
