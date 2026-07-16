"""
Confidence-weight configuration loader.

Weights live in `backend/config/resolution_weights.json`, never in Python
(Phase 3 brief SS7: "Do NOT hardcode weights. Load weights from
configuration."). This module loads, validates, and caches them.

Validation is strict and fail-fast: a missing key or a negative weight raises
at load time rather than silently scoring everything wrong. A compliance
system that quietly mis-weights evidence is worse than one that refuses to
start.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.core.config import BACKEND_DIR, get_settings

DEFAULT_WEIGHTS_PATH = BACKEND_DIR / "config" / "resolution_weights.json"

REQUIRED_SCORERS = frozenset(
    {
        "name",
        "alias",
        "country",
        "nationality",
        "entity_type",
        "dob",
        "identifier",
        "ownership",
        "organization",
    }
)


class ResolutionThresholds(BaseModel):
    name_floor: float = Field(ge=0.0, le=1.0)
    high_confidence: float = Field(ge=0.0, le=100.0)
    possible: float = Field(ge=0.0, le=100.0)
    rejected_below: float = Field(ge=0.0, le=100.0)


class ScorerThresholds(BaseModel):
    name_match: float = Field(ge=0.0, le=1.0)
    alias_match: float = Field(ge=0.0, le=1.0)
    organization_match: float = Field(ge=0.0, le=1.0)
    partial_dob_year_credit: float = Field(ge=0.0, le=1.0)


class ResolutionWeights(BaseModel):
    positive_weights: dict[str, float]
    conflict_penalties: dict[str, float]
    thresholds: ResolutionThresholds
    scorer_thresholds: ScorerThresholds

    @field_validator("positive_weights")
    @classmethod
    def _validate_positive(cls, value: dict[str, float]) -> dict[str, float]:
        missing = REQUIRED_SCORERS - set(value)
        if missing:
            raise ValueError(f"resolution weights missing positive_weights for: {sorted(missing)}")
        negative = [k for k, v in value.items() if v < 0]
        if negative:
            raise ValueError(f"positive_weights must be >= 0; got negative for: {sorted(negative)}")
        return value

    @field_validator("conflict_penalties")
    @classmethod
    def _validate_penalties(cls, value: dict[str, float]) -> dict[str, float]:
        negative = [k for k, v in value.items() if v < 0]
        if negative:
            raise ValueError(
                f"conflict_penalties are subtracted, so they must be expressed as positive "
                f"magnitudes; got negative for: {sorted(negative)}"
            )
        return value

    def positive_weight(self, scorer: str) -> float:
        return self.positive_weights.get(scorer, 0.0)

    def conflict_penalty(self, scorer: str) -> float:
        return self.conflict_penalties.get(scorer, 0.0)

    @property
    def max_positive_total(self) -> float:
        return sum(self.positive_weights.values())


def load_weights(path: Path | None = None) -> ResolutionWeights:
    """Load and validate weights from disk. Raises on a missing or malformed
    file -- never falls back to silent in-code defaults, which would defeat
    the point of externalizing them."""
    settings = get_settings()
    resolved = path or getattr(settings, "resolution_weights_path", None) or DEFAULT_WEIGHTS_PATH
    resolved = Path(resolved)
    if not resolved.is_file():
        raise FileNotFoundError(
            f"Resolution weights config not found at {resolved}. "
            "Set RESOLUTION_WEIGHTS_PATH or restore backend/config/resolution_weights.json."
        )
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    return ResolutionWeights(**raw)


@lru_cache
def get_weights() -> ResolutionWeights:
    return load_weights()
