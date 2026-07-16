"""Case-management API contracts (Phase 6 brief SS9)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.casework.schemas import CaseMetrics, CaseSummary, CaseTimeline
from app.core.enums import ActorType, CaseStatus, ReviewAction, SARStatus
from app.models.review import HumanReview
from app.models.sar import SARDraft
from app.schemas.base import ORMReadModel


class OpenCaseRequest(BaseModel):
    external_client_id: int
    title: str | None = None
    reason: str | None = None
    alert_id: int | None = None
    investigation_id: int | None = None
    assigned_to: str | None = None


class ReviewRequest(BaseModel):
    """`reviewer` is required and has no default.

    Deliberately not optional and never defaulted to "system" or "unknown": an
    unattributed compliance decision is not a compliance decision. Every review
    must name the person accountable for it.
    """

    reviewer: str = Field(min_length=1, description="The person making this decision. Required.")
    action: ReviewAction
    comment: str | None = Field(default=None, description="Stored as HumanReview.rationale.")
    target_type: str | None = None
    target_id: int | None = Field(
        default=None,
        description="The record being decided on (EntityMatch id for CONFIRM/REJECT_MATCH; SARDraft id for SAR actions).",
    )


class GenerateSARRequest(BaseModel):
    requested_by: str = Field(
        min_length=1, description="Who asked for the draft. Required, for the audit trail."
    )


class HumanReviewRead(ORMReadModel):
    id: int
    reviewer_name: str
    action: ReviewAction
    comment: str | None = None
    decided_at: datetime
    previous_state: CaseStatus | None = None
    new_state: CaseStatus | None = None
    target_type: str | None = None
    target_id: int | None = None


class AuditEntryRead(ORMReadModel):
    id: int
    created_at: datetime
    actor_type: ActorType
    actor_id: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    reason: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    correlation_id: str | None = None


class SARSectionRead(BaseModel):
    key: str
    title: str
    body: str
    generated_by: str
    evidence_ids: list[int] = Field(default_factory=list)


class SARDraftRead(ORMReadModel):
    id: int
    sar_ref: str | None = None
    case_id: int | None = None
    client_id: int
    investigation_id: int | None = None
    status: SARStatus
    generated_at: datetime | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    marking: str = "DRAFT -- NOT FILED -- REQUIRES HUMAN APPROVAL"
    requires_human_approval: bool = True
    sections: list[SARSectionRead] = Field(default_factory=list)
    content: str | None = None
    cited_evidence_ids: list[int] = Field(default_factory=list)
    grounding_passed: bool | None = None
    hallucinated_citation_count: int | None = None
    narrative_generated_by: str | None = None
    narrative_model: str | None = None
    prompt_version: str | None = None
    narrative_error: str | None = None


class ActionRequirement(BaseModel):
    """What one permitted action needs before the server will accept it.

    `available_actions` already tells a caller WHICH actions are legal, on the
    stated principle that "a caller never has to guess" (state_machine.py). But
    `requires_target` was left server-side, so a caller still had to guess THAT
    -- and the Phase 7 UI guessed by hand-copying `_ACTION_RULES`, missed
    APPROVE and REJECT, and offered a form the server then rejected. The copy
    was the bug; a second copy that happens to be correct today would drift the
    moment a rule changes.

    So this exposes the rule itself. The state machine stays the single source
    of truth and the UI reads it, exactly as it already reads available_actions.
    """

    action: ReviewAction
    requires_target: bool = Field(
        description="If true, the review MUST carry target_id -- the id of the record being decided on."
    )
    target_type: str | None = Field(
        default=None,
        description="Which record target_id refers to (e.g. SARDraft, EntityMatch). Null when none is needed.",
    )
    description: str = Field(description="The rule's own description, verbatim from the state machine.")


class CaseDetailResponse(BaseModel):
    """The workspace (brief SS2). Aggregated at request time from the live rows;
    nothing is copied onto the case, so nothing here can be stale."""

    case: CaseSummary
    available_actions: list[ReviewAction] = Field(
        default_factory=list,
        description="What this reviewer may do right now, from the state machine. A caller never guesses.",
    )
    action_requirements: list[ActionRequirement] = Field(
        default_factory=list,
        description=(
            "Per-action contract for `available_actions`, in the same order. Tells a caller which "
            "actions need a target_id, so no client has to reimplement the state machine's rules."
        ),
    )
    customer: dict[str, Any] | None = None
    risk_current: dict[str, Any] | None = None
    risk_history: list[dict[str, Any]] = Field(default_factory=list)
    risk_events: list[dict[str, Any]] = Field(default_factory=list)
    entity_matches: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    alerts: list[dict[str, Any]] = Field(default_factory=list)
    investigations: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[HumanReviewRead] = Field(default_factory=list)
    sar_drafts: list[SARDraftRead] = Field(default_factory=list)
    human_decision_required: bool = Field(
        default=True,
        description="Always true for a non-closed case. This system never decides.",
    )


class CaseListResponse(BaseModel):
    cases: list[CaseSummary] = Field(default_factory=list)
    total: int


class CaseTimelineResponse(BaseModel):
    timeline: CaseTimeline


class CaseAuditResponse(BaseModel):
    case_id: int
    entries: list[AuditEntryRead] = Field(default_factory=list)
    total: int
    note: str = (
        "Immutable. Audit records are never updated or deleted -- the repository exposes no "
        "method that could."
    )


def _decode(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def review_read(review: HumanReview) -> HumanReviewRead:
    return HumanReviewRead(
        id=review.id,
        reviewer_name=review.reviewer_name,
        action=review.action,
        comment=review.rationale,
        decided_at=review.decided_at,
        previous_state=review.previous_state,
        new_state=review.new_state,
        target_type=review.target_type,
        target_id=review.target_id,
    )


def sar_read(sar: SARDraft, *, include_content: bool = True) -> SARDraftRead:
    sections = _decode(sar.sections_json) or []
    return SARDraftRead(
        id=sar.id,
        sar_ref=sar.sar_ref,
        case_id=sar.case_id,
        client_id=sar.client_id,
        investigation_id=sar.investigation_id,
        status=sar.status,
        generated_at=sar.generated_at,
        reviewed_by=sar.reviewed_by,
        reviewed_at=sar.reviewed_at,
        sections=[SARSectionRead(**s) for s in sections if isinstance(s, dict)],
        content=sar.content if include_content else None,
        cited_evidence_ids=_decode(sar.cited_evidence_ids_json) or [],
        grounding_passed=sar.grounding_passed,
        hallucinated_citation_count=sar.hallucinated_citation_count,
        narrative_generated_by=sar.narrative_generated_by,
        narrative_model=sar.narrative_model,
        prompt_version=sar.prompt_version,
        narrative_error=sar.narrative_error,
    )


__all__ = [
    "AuditEntryRead",
    "CaseAuditResponse",
    "CaseDetailResponse",
    "CaseListResponse",
    "CaseMetrics",
    "CaseTimelineResponse",
    "GenerateSARRequest",
    "HumanReviewRead",
    "OpenCaseRequest",
    "ReviewRequest",
    "SARDraftRead",
    "SARSectionRead",
    "review_read",
    "sar_read",
]
