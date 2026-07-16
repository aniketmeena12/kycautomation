"""
Alert engine -- pure threshold/transition rules, config-driven.

Proposes `AlertProposal`s; the monitoring service persists them. Keeping the
rules free of persistence means they are testable with no database at all,
and it keeps "when should we alert?" (policy) separate from "how do we store
an alert?" (plumbing).

--------------------------------------------------------------------------
WHY ALERTS ARE ABOUT CHANGE, NOT STATE
--------------------------------------------------------------------------
A client sitting at CRITICAL for a month with nothing new must not re-alert
every cycle -- that is how alert fatigue starts, and a queue nobody reads is
worse than no queue. So every rule keys on a TRANSITION or a NEW finding:

  BAND_ESCALATION  -- band moved UP into a configured escalation band.
                      Deliberately not "is in" -- "moved into".
  SCORE_DELTA      -- score rose by >= min_score_delta.
  CRITICAL_EVENT   -- a NEW event of a configured critical type appeared.
  REPEATED_SIGNAL  -- N+ new events of the same type in one cycle
                      (corroboration is itself a signal).
  PROVIDER_DEGRADED-- a provider failed, so coverage was incomplete.

Each proposal's `dedup_key` is stable for the finding, so the DB unique
constraint (client_id, dedup_key) makes duplicate suppression structural
rather than a best-effort check.
"""

from __future__ import annotations

from collections import Counter

from app.core.enums import AlertTrigger, RiskBand
from app.models.risk import RiskEvent
from app.risk.config import RiskRegistry, get_risk_registry
from app.risk.schemas import AlertProposal, RiskScoreResult

_BAND_ORDER = {RiskBand.LOW: 0, RiskBand.MEDIUM: 1, RiskBand.HIGH: 2, RiskBand.CRITICAL: 3}


def band_rank(band: RiskBand | None) -> int:
    return _BAND_ORDER.get(band, -1) if band is not None else -1


class AlertEngine:
    def __init__(self, registry: RiskRegistry | None = None) -> None:
        self._registry = registry or get_risk_registry()

    @property
    def config(self):
        return self._registry.alerts

    def propose(self, risk: RiskScoreResult, new_events: list[RiskEvent]) -> list[AlertProposal]:
        proposals: list[AlertProposal] = []
        proposals.extend(self._band_escalation(risk))
        proposals.extend(self._score_delta(risk))
        proposals.extend(self._critical_events(new_events))
        proposals.extend(self._repeated_signals(new_events))
        proposals.extend(self._provider_degraded(new_events))
        return proposals

    def _band_escalation(self, risk: RiskScoreResult) -> list[AlertProposal]:
        cfg = self._registry.alerts
        if risk.band not in cfg.escalation_bands:
            return []
        # A first-ever snapshot has no previous band. Alerting on it is
        # correct -- an entity that starts at HIGH is news -- but it is an
        # escalation from "unknown", not from LOW, and the reason says so.
        if band_rank(risk.band) <= band_rank(risk.previous_band):
            return []

        previous = risk.previous_band.value if risk.previous_band else "no prior assessment"
        return [
            AlertProposal(
                severity=risk.band,
                trigger=AlertTrigger.BAND_ESCALATION,
                reason=(
                    f"Risk band escalated from {previous} to {risk.band.value} "
                    f"(score {risk.previous_score if risk.previous_score is not None else 'n/a'} -> {risk.score:.0f})."
                ),
                risk_delta=risk.delta,
                # Keyed on the transition, so the same escalation re-proposed
                # next cycle collapses onto the existing alert.
                dedup_key=f"band_escalation:{previous}->{risk.band.value}",
            )
        ]

    def _score_delta(self, risk: RiskScoreResult) -> list[AlertProposal]:
        cfg = self._registry.alerts
        if risk.delta is None or risk.delta < cfg.min_score_delta:
            return []
        return [
            AlertProposal(
                severity=risk.band,
                trigger=AlertTrigger.SCORE_DELTA,
                reason=(
                    f"Risk score rose {risk.delta:.0f} pts "
                    f"({risk.previous_score:.0f} -> {risk.score:.0f}), at or above the "
                    f"{cfg.min_score_delta:.0f}-pt alerting threshold."
                ),
                risk_delta=risk.delta,
                dedup_key=f"score_delta:{risk.previous_score:.0f}->{risk.score:.0f}",
            )
        ]

    def _critical_events(self, new_events: list[RiskEvent]) -> list[AlertProposal]:
        cfg = self._registry.alerts
        out: list[AlertProposal] = []
        for event in new_events:
            if event.event_type not in cfg.critical_event_types:
                continue
            out.append(
                AlertProposal(
                    severity=event.severity,
                    trigger=AlertTrigger.CRITICAL_EVENT,
                    reason=f"Critical event detected: {event.event_type.value}. {event.summary or ''}".strip(),
                    dedup_key=f"critical_event:{event.dedup_key}",
                    risk_event_ids=[event.id] if event.id else [],
                    evidence_ids=[e.id for e in event.evidence],
                )
            )
        return out

    def _repeated_signals(self, new_events: list[RiskEvent]) -> list[AlertProposal]:
        """Repetition of the SAME KIND of finding is corroboration.

        Excluded types come from config: OTHER is a catch-all shared by
        unrelated factors, so counting it fires a false "repetition" alert for
        two entirely different facts (observed in live testing: high-risk
        sector + ownership opacity both land in OTHER).
        """
        cfg = self._registry.alerts
        excluded = set(cfg.repeated_signal_excluded_event_types)
        counts = Counter(e.event_type for e in new_events if e.event_type not in excluded)
        out: list[AlertProposal] = []
        for event_type, count in counts.items():
            if count < cfg.repeated_signal_threshold:
                continue
            related = [e for e in new_events if e.event_type == event_type]
            severity = max((e.severity for e in related), key=band_rank)
            out.append(
                AlertProposal(
                    severity=severity,
                    trigger=AlertTrigger.REPEATED_SIGNAL,
                    reason=(
                        f"{count} new {event_type.value} findings in one monitoring cycle "
                        f"(threshold {cfg.repeated_signal_threshold}). Repetition is corroboration."
                    ),
                    dedup_key=f"repeated:{event_type.value}:{count}",
                    risk_event_ids=[e.id for e in related if e.id],
                )
            )
        return out

    def _provider_degraded(self, new_events: list[RiskEvent]) -> list[AlertProposal]:
        from app.core.enums import RiskEventType

        cfg = self._registry.alerts
        if not cfg.alert_on_provider_failure:
            return []
        out: list[AlertProposal] = []
        for event in new_events:
            if event.event_type != RiskEventType.PROVIDER_FAILURE:
                continue
            out.append(
                AlertProposal(
                    # LOW severity by design: incomplete coverage is an
                    # operational problem, not evidence against the client.
                    severity=RiskBand.LOW,
                    trigger=AlertTrigger.PROVIDER_DEGRADED,
                    reason=f"Monitoring coverage incomplete. {event.summary or ''}".strip(),
                    dedup_key=f"provider_degraded:{event.dedup_key}",
                    risk_event_ids=[event.id] if event.id else [],
                )
            )
        return out
