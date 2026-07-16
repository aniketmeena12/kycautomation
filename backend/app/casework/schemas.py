"""Case-management contracts (Phase 6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import ActorType, CaseStatus, TimelineEntryType


class TimelineEntry(BaseModel):
    """One thing that happened, DERIVED from a stored row (brief SS3).

    `entry_key` is the deduplication identity: `"{type}:{source_id}"`. It is a
    fingerprint of the SOURCE ROW, never of the render -- the same discipline as
    Phase 4's `dedup_key` and Phase 5's `context_hash`. Keying on the rendered
    title would make two genuinely distinct events with identical wording
    collapse into one, silently deleting history from a compliance timeline.
    """

    entry_key: str
    timestamp: datetime
    entry_type: TimelineEntryType
    title: str
    summary: str | None = None

    # Who or what caused it. Every entry has an actor, because "the system
    # observed this", "the agent wrote this", and "a person decided this" are
    # the three facts an auditor most needs separated.
    actor_type: ActorType
    actor_id: str | None = None

    related_entity: str | None = None
    related_evidence_ids: list[int] = Field(default_factory=list)
    related_event_id: int | None = None

    source_table: str
    source_id: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseTimeline(BaseModel):
    case_id: int
    entries: list[TimelineEntry] = Field(default_factory=list)
    total: int = 0
    generated_at: datetime
    # Counts by entry_type -- lets a caller see coverage at a glance without
    # walking the list.
    counts_by_type: dict[str, int] = Field(default_factory=dict)


class CaseMetrics(BaseModel):
    """Brief SS8. Operational counts only.

    Deliberately no "SAR approval rate", no "reviewer accuracy", no quality
    score. Phase 0 SS14 established this dataset cannot support calibration, and
    a metric that rates a human reviewer's judgement against an unvalidated
    baseline would be exactly the unearned number this project has refused
    since then.
    """

    open_cases: int = 0
    under_review_cases: int = 0
    escalated_cases: int = 0
    sar_review_cases: int = 0
    closed_cases: int = 0
    total_cases: int = 0

    high_risk_cases: int = Field(
        default=0, description="Open cases whose client's latest snapshot is in an escalation band."
    )
    sar_pending: int = Field(default=0, description="SAR drafts awaiting a human decision.")
    sar_approved: int = 0
    sar_rejected: int = 0

    human_review_count: int = 0
    human_reviews_by_action: dict[str, int] = Field(default_factory=dict)

    average_investigation_latency_ms: float | None = Field(
        default=None,
        description=(
            "Mean LLM latency over investigations that actually produced a report. "
            "Null when none have -- not 0.0, which would read as 'instant'."
        ),
    )
    investigations_total: int = 0
    investigations_failed: int = 0

    generated_at: datetime


class CaseSummary(BaseModel):
    """List-view projection. Deliberately thin: /cases is a queue, and a queue
    that eagerly loads every client's full evidence graph is a queue nobody can
    open."""

    id: int
    case_ref: str
    client_id: int
    external_client_id: int
    client_name: str
    status: CaseStatus
    title: str | None = None
    assigned_to: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    current_risk_score: float | None = None
    current_risk_band: str | None = None
    open_alert_count: int = 0
    investigation_count: int = 0
    review_count: int = 0
    has_sar_draft: bool = False


__all__ = ["CaseMetrics", "CaseSummary", "CaseTimeline", "TimelineEntry"]
