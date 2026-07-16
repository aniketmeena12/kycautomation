"""
Risk Factor Registry loader.

Factors live in `backend/config/risk_factors.json`, never in Python
(Phase 4 brief SS2: "Do NOT hardcode weights. Load from configuration.
Support future expansion without code changes.").

"Without code changes" is literal: `RiskFactor.matches(signal)` evaluates a
declarative `trigger_condition` against a signal's attributes. Adding a
factor is appending an object to the JSON -- the engine discovers it by
matching, and the explainability layer reports it by name. Nothing in
app/risk/engine.py knows any factor id.

`trigger_condition` is deliberately a small declarative spec (signal_type /
min_confidence / metadata_equals), NOT an expression language. A config file
that can execute arbitrary logic is a config file that can be an injection
vector -- and this project's standing rule is that data is never
instructions (docs/phase-1-foundation.md). Anything richer than this belongs
in a signal collector, which is code and gets reviewed.

Validation is fail-fast, mirroring ADR-011: a malformed registry raises at
load rather than silently scoring everything wrong.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.core.config import BACKEND_DIR, get_settings
from app.core.enums import RiskBand, RiskEventType

DEFAULT_RISK_FACTORS_PATH = BACKEND_DIR / "config" / "risk_factors.json"

ContributionFormula = Literal["weight_x_confidence", "weight_only"]


class TriggerCondition(BaseModel):
    """Declarative match spec. All present fields are ANDed."""

    signal_type: str | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata_equals: dict[str, Any] = Field(default_factory=dict)

    def matches(self, signal) -> bool:
        if self.signal_type is not None and signal.signal_type != self.signal_type:
            return False
        if self.min_confidence is not None and signal.confidence < self.min_confidence:
            return False
        for key, expected in self.metadata_equals.items():
            if signal.metadata.get(key) != expected:
                return False
        return True


class RiskFactor(BaseModel):
    id: str
    name: str
    description: str
    category: str
    severity: RiskBand
    weight: float = Field(ge=0.0)
    confidence_multiplier: float = Field(default=1.0, ge=0.0)
    max_contribution: float = Field(ge=0.0)
    requires_entity_resolution: bool = False
    enabled: bool = True
    event_type: RiskEventType = RiskEventType.OTHER
    trigger_condition: TriggerCondition

    def matches(self, signal) -> bool:
        return self.enabled and self.trigger_condition.matches(signal)


class ScoringConfig(BaseModel):
    contribution_formula: ContributionFormula = "weight_x_confidence"
    max_total_score: float = Field(default=100.0, gt=0.0)
    scoring_logic_version: str


class AlertConfig(BaseModel):
    model_config = {"extra": "ignore"}  # tolerate `_note` keys in the JSON

    escalation_bands: list[RiskBand] = Field(default_factory=list)
    min_score_delta: float = Field(default=15.0, ge=0.0)
    critical_event_types: list[RiskEventType] = Field(default_factory=list)
    repeated_signal_threshold: int = Field(default=2, ge=1)
    # Event types that must NOT count toward REPEATED_SIGNAL. OTHER is a
    # catch-all shared by unrelated factors, so counting it fires a false
    # "repetition" alert for two entirely different findings -- observed in
    # live testing. See the note in config/risk_factors.json.
    repeated_signal_excluded_event_types: list[RiskEventType] = Field(default_factory=list)
    alert_on_provider_failure: bool = True


class RiskRegistry(BaseModel):
    scoring: ScoringConfig
    bands: dict[RiskBand, float]
    alerts: AlertConfig
    factors: list[RiskFactor]

    @field_validator("factors")
    @classmethod
    def _validate_factors(cls, value: list[RiskFactor]) -> list[RiskFactor]:
        if not value:
            raise ValueError("risk_factors.json must define at least one factor.")
        ids = [f.id for f in value]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"Duplicate risk factor ids: {sorted(duplicates)}")
        for factor in value:
            if factor.max_contribution > factor.weight and factor.weight > 0:
                raise ValueError(
                    f"Factor '{factor.id}': max_contribution ({factor.max_contribution}) exceeds "
                    f"weight ({factor.weight}); the cap would never bind."
                )
        return value

    @field_validator("bands")
    @classmethod
    def _validate_bands(cls, value: dict[RiskBand, float]) -> dict[RiskBand, float]:
        required = {RiskBand.LOW, RiskBand.MEDIUM, RiskBand.HIGH, RiskBand.CRITICAL}
        missing = required - set(value)
        if missing:
            raise ValueError(f"bands config missing thresholds for: {sorted(b.value for b in missing)}")
        ordered = [
            value[RiskBand.LOW],
            value[RiskBand.MEDIUM],
            value[RiskBand.HIGH],
            value[RiskBand.CRITICAL],
        ]
        if ordered != sorted(ordered):
            raise ValueError(f"band thresholds must ascend LOW<=MEDIUM<=HIGH<=CRITICAL; got {ordered}")
        return value

    def enabled_factors(self) -> list[RiskFactor]:
        return [f for f in self.factors if f.enabled]

    def factor_by_id(self, factor_id: str) -> RiskFactor | None:
        return next((f for f in self.factors if f.id == factor_id), None)

    def band_for(self, score: float) -> RiskBand:
        """Highest band whose threshold the score meets."""
        band = RiskBand.LOW
        for candidate in (RiskBand.LOW, RiskBand.MEDIUM, RiskBand.HIGH, RiskBand.CRITICAL):
            if score >= self.bands[candidate]:
                band = candidate
        return band


def load_risk_registry(path: Path | None = None) -> RiskRegistry:
    settings = get_settings()
    resolved = Path(path or getattr(settings, "risk_factors_path", None) or DEFAULT_RISK_FACTORS_PATH)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Risk factor registry not found at {resolved}. "
            "Set RISK_FACTORS_PATH or restore backend/config/risk_factors.json."
        )
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    return RiskRegistry(**raw)


@lru_cache
def get_risk_registry() -> RiskRegistry:
    return load_risk_registry()
