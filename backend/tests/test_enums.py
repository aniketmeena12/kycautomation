"""Enum values serialize predictably in both Pydantic schemas and raw JSON."""

import json

from app.core.enums import ProviderResultStatus, RiskBand, SourceTier
from app.schemas.risk import RiskScoreSnapshotRead


def test_source_tier_values_are_stable_strings():
    assert SourceTier.TIER_1_AUTHORITATIVE.value == "TIER_1_AUTHORITATIVE"
    assert SourceTier.TIER_2_CURATED_DEMO.value == "TIER_2_CURATED_DEMO"
    assert SourceTier.EXTERNAL_LIVE.value == "EXTERNAL_LIVE"


def test_provider_result_status_covers_graceful_degradation_cases():
    values = {s.value for s in ProviderResultStatus}
    assert {"SUCCESS", "NO_RESULTS", "NOT_CONFIGURED", "RATE_LIMITED", "TIMEOUT", "ERROR"} == values


def test_pydantic_schema_serializes_enum_as_plain_string():
    from datetime import datetime, timezone

    snapshot = RiskScoreSnapshotRead(
        id=1,
        client_id=1,
        previous_score=None,
        current_score=42.0,
        risk_band=RiskBand.MEDIUM,
        computed_at=datetime.now(timezone.utc),
        trigger_reason=None,
        scoring_logic_version=None,
    )
    payload = json.loads(snapshot.model_dump_json())
    assert payload["risk_band"] == "MEDIUM"
