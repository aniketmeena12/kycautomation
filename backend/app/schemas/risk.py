"""Risk / monitoring / alert API contracts.

Extends the Phase 1 read schemas rather than replacing them -- RiskEventRead
and RiskScoreSnapshotRead keep their original fields and gain the Phase 4
ones.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.enums import AlertStatus, AlertTrigger, RiskBand, RiskEventStatus, RiskEventType
from app.risk.config import RiskFactor
from app.risk.schemas import MonitoringCycleResult, MonitoringRunResult, RiskScoreResult
from app.schemas.base import ORMReadModel


class MonitorClientRequest(BaseModel):
    include_providers: bool = True
    include_resolution: bool = True
    allow_expensive_providers: bool = Field(
        default=False,
        description="Permit the Tier-1 OpenSanctions provider (~40-45s / 1.3M rows). Off by default.",
    )


class MonitorAllRequest(MonitorClientRequest):
    limit: int = Field(default=25, ge=1, le=500, description="Clients per sweep. Monitoring is synchronous.")
    offset: int = Field(default=0, ge=0)
    external_client_ids: list[int] | None = Field(
        default=None, description="Monitor exactly these clients. Overrides limit/offset."
    )
    high_risk_only: bool = Field(
        default=False, description="Only clients whose latest snapshot is in a configured escalation band."
    )


class RiskEventRead(ORMReadModel):
    id: int
    client_id: int
    event_type: RiskEventType
    severity: RiskBand
    confidence: float
    status: RiskEventStatus
    event_timestamp: datetime | None = None
    detected_at: datetime
    # Phase 4 additions. dedup_key/source are non-null in the DB, but are
    # defaulted here so a caller constructing this schema with the Phase 1
    # field set still validates -- additive, exactly like the model change.
    dedup_key: str | None = None
    source: str | None = None
    trigger: str | None = None
    summary: str | None = None
    entity_ref: str | None = None
    factor_id: str | None = None


class RiskEventListResponse(BaseModel):
    events: list[RiskEventRead]
    total: int


class RiskScoreSnapshotRead(ORMReadModel):
    id: int
    client_id: int
    previous_score: float | None = None
    current_score: float
    risk_band: RiskBand
    computed_at: datetime
    trigger_reason: str | None = None
    scoring_logic_version: str | None = None
    # Phase 4 additions. Defaulted to None to match the DB, where all three
    # are nullable -- a first-ever snapshot genuinely has no previous band or
    # delta. Making them required would also break any caller constructing
    # this schema with the Phase 1 field set.
    previous_band: RiskBand | None = None
    delta: float | None = None
    factor_contributions: str | None = None


class RiskHistoryResponse(BaseModel):
    client_id: int
    external_client_id: int
    snapshots: list[RiskScoreSnapshotRead]
    total: int


class CurrentRiskResponse(BaseModel):
    """The latest stored assessment. `null` current when a client has never
    been monitored -- deliberately not defaulted to 0/LOW, which would assert
    'we assessed them and they're fine' when we never looked."""

    client_id: int
    external_client_id: int
    current: RiskScoreSnapshotRead | None = None
    never_monitored: bool = False


class AlertRead(ORMReadModel):
    id: int
    client_id: int
    status: AlertStatus
    severity: RiskBand
    trigger: AlertTrigger
    reason: str | None
    risk_delta: float | None
    dedup_key: str
    triggering_risk_event_id: int | None
    opened_at: datetime
    closed_at: datetime | None


class AlertListResponse(BaseModel):
    alerts: list[AlertRead]
    total: int


class AlertDetailResponse(BaseModel):
    alert: AlertRead
    risk_events: list[RiskEventRead] = []
    evidence_ids: list[int] = []


class RiskFactorRead(BaseModel):
    """The registry, exposed. Makes the scoring model inspectable without
    reading the config file off disk -- explainability extends to *why the
    engine is configured the way it is*, not just to individual scores."""

    id: str
    name: str
    description: str
    category: str
    severity: RiskBand
    weight: float
    confidence_multiplier: float
    max_contribution: float
    requires_entity_resolution: bool
    enabled: bool
    event_type: RiskEventType


class RiskFactorListResponse(BaseModel):
    factors: list[RiskFactorRead]
    total: int
    enabled_count: int
    contribution_formula: str
    scoring_logic_version: str
    bands: dict[str, float]


__all__ = [
    "MonitorClientRequest",
    "MonitorAllRequest",
    "RiskEventRead",
    "RiskEventListResponse",
    "RiskScoreSnapshotRead",
    "RiskHistoryResponse",
    "CurrentRiskResponse",
    "AlertRead",
    "AlertListResponse",
    "AlertDetailResponse",
    "RiskFactorRead",
    "RiskFactorListResponse",
    "MonitoringCycleResult",
    "MonitoringRunResult",
    "RiskScoreResult",
    "RiskFactor",
]
