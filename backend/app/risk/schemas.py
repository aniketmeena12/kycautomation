"""
Risk-intelligence contracts.

`RiskSignal` is the generic input to the whole engine. A signal collector
(app/risk/signals.py) turns *anything* -- a resolution result, a profile flag,
a provider failure -- into one of these, and the risk engine only ever sees
RiskSignals. That containment is what makes the engine generic and
factor-registry-driven: a new factor needs a signal with a matching
`signal_type`, not a code change in the engine.

`dedup_key` is what makes monitoring event-driven rather than repetitive: it
is a stable fingerprint of the *finding*, so re-running a cycle over unchanged
data produces the same keys and therefore no new events (Phase 4 brief SS9).
It must be derived from the finding's identity, never from a timestamp or a
run id.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import AlertTrigger, RiskBand, RiskEventType


class RiskSignal(BaseModel):
    """One observation about a client, from any source."""

    signal_type: str = Field(description="Matched against a factor's trigger_condition.signal_type.")
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = Field(description="Where it came from, e.g. a provider name or 'internal_kyc'.")
    summary: str
    dedup_key: str = Field(description="Stable fingerprint of the FINDING -- never includes a timestamp.")
    occurred_at: datetime
    evidence_ids: list[int] = Field(default_factory=list)
    entity_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FactorContribution(BaseModel):
    """One factor's contribution to a score, fully itemized -- no opaque
    calculations (Phase 4 brief SS4)."""

    factor_id: str
    factor_name: str
    category: str
    severity: RiskBand
    weight: float
    signal_confidence: float
    confidence_multiplier: float
    raw_contribution: float = Field(description="Before max_contribution capping.")
    contribution: float = Field(description="Final points added to the score.")
    capped: bool = False
    reason: str
    signal_source: str
    evidence_ids: list[int] = Field(default_factory=list)
    event_type: RiskEventType


class RiskScoreResult(BaseModel):
    client_id: int
    score: float = Field(ge=0.0, le=100.0)
    band: RiskBand
    previous_score: float | None = None
    previous_band: RiskBand | None = None
    delta: float | None = None
    contributions: list[FactorContribution] = Field(default_factory=list)
    unmatched_signals: list[str] = Field(default_factory=list)
    total_before_cap: float = 0.0
    capped_at_max: bool = False
    scoring_logic_version: str
    computed_at: datetime
    explanation: str


class AlertProposal(BaseModel):
    """The alert engine proposes; the monitoring service persists. Keeps alert
    *rules* independent of persistence, so they're testable in isolation."""

    severity: RiskBand
    trigger: AlertTrigger
    reason: str
    risk_delta: float | None = None
    dedup_key: str
    risk_event_ids: list[int] = Field(default_factory=list)
    evidence_ids: list[int] = Field(default_factory=list)


class MonitoringCycleResult(BaseModel):
    client_id: int
    external_client_id: int | None = None
    signals_collected: int = 0
    new_events: int = 0
    suppressed_duplicate_events: int = 0
    risk: RiskScoreResult | None = None
    alerts_created: int = 0
    providers_queried: list[str] = Field(default_factory=list)
    provider_failures: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
    error: str | None = None


class MonitoringRunResult(BaseModel):
    cycles: list[MonitoringCycleResult] = Field(default_factory=list)
    clients_monitored: int = 0
    clients_failed: int = 0
    total_new_events: int = 0
    total_alerts: int = 0
    started_at: datetime
    completed_at: datetime
