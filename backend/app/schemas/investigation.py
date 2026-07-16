"""
Investigation API contracts.

Phase 1's read schemas are EXTENDED, never replaced: every field it declared
keeps its name and type, and every Phase 5 addition carries a default. This is
the direct lesson of the Phase 4 regression, where new snapshot fields were
made required in Pydantic while nullable in the DB and a Phase 1 test caught
it -- the schema was wrong, not the test. Additive-with-defaults means a caller
constructing these with the Phase 1 field set still validates.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import (
    GroundingStatus,
    InvestigationFindingType,
    InvestigationRecommendationAction,
    InvestigationStatus,
)
from app.models.investigation import Investigation
from app.schemas.base import ORMReadModel


class InvestigationFindingRead(ORMReadModel):
    id: int
    evidence_id: int | None
    finding_text: str
    created_at: datetime

    # --- Phase 5 additions (defaulted; see module docstring) ---
    finding_type: InvestigationFindingType | None = None
    confidence_statement: str | None = None
    # The deterministic verdict. UNGROUNDED means this finding cited evidence
    # that does not exist -- it is surfaced, not hidden, because a reviewer
    # needs to know the model hallucinated on this file.
    grounding_status: GroundingStatus | None = None
    cited_evidence_ids: list[int] = Field(default_factory=list)
    invalid_evidence_ids: list[int] = Field(default_factory=list)


class InvestigationRecommendationRead(ORMReadModel):
    """`action` is enum-typed. APPROVE/REJECT are not members, so this schema
    cannot express a final compliance decision (ADR-027)."""

    id: int
    action: InvestigationRecommendationAction
    rationale: str
    cited_evidence_ids: list[int] = Field(default_factory=list)


class InvestigationRead(ORMReadModel):
    id: int
    client_id: int
    status: InvestigationStatus
    trigger_snapshot_id: int | None
    opened_at: datetime
    closed_at: datetime | None
    summary: str | None
    findings: list[InvestigationFindingRead] = []

    # --- Phase 5 additions ---
    trigger_reason: str | None = None
    triggering_alert_id: int | None = None
    error_message: str | None = None


class RunInvestigationRequest(BaseModel):
    trigger_reason: str | None = Field(
        default=None, description="Why this investigation was opened. Stored, and shown to the agent."
    )
    alert_id: int | None = Field(
        default=None,
        description="Investigate the client behind this alert. Takes precedence over trigger_reason.",
    )


class InvestigationEvaluationRead(BaseModel):
    """Operational metrics only (brief SS10).

    Every field is a fact about the run -- which model, how long, how many
    tokens, how much of the available evidence was actually used. Nothing here
    scores the report's quality: inventing a number to rate an LLM's output
    would be exactly the unearned metric this project has refused since Phase 0
    SS14 established the dataset cannot support calibration.
    """

    prompt_version: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    temperature: float | None = Field(
        default=None,
        description=(
            "Null on current models: they REJECT sampling parameters (HTTP 400) rather than "
            "defaulting them, so none was sent. Null is the honest record of a parameter that "
            "was never part of the request. See ADR-025."
        ),
    )
    context_hash: str | None = None
    generated_at: datetime | None = None

    # Evidence Used / Ignored / Missing / Conflicting (brief SS10).
    evidence_available_count: int | None = None
    evidence_used_count: int | None = None
    evidence_ignored_count: int | None = None
    missing_information_count: int | None = None
    conflicting_evidence_count: int | None = None

    grounding_passed: bool | None = None
    hallucinated_citation_count: int | None = None
    ungrounded_finding_count: int | None = None
    injection_flags: list[str] = Field(default_factory=list)


class InvestigationDetailResponse(BaseModel):
    investigation: InvestigationRead
    report: dict[str, Any] | None = Field(
        default=None, description="The agent's structured report, exactly as validated and stored."
    )
    recommendations: list[InvestigationRecommendationRead] = []
    evaluation: InvestigationEvaluationRead
    grounding: dict[str, Any] | None = Field(
        default=None, description="Full deterministic grounding verdict (app/investigation/grounding.py)."
    )
    human_review_required: bool = Field(
        default=True,
        description=(
            "Always true. This system never closes or decides an investigation -- a human does. "
            "Stated explicitly so no caller infers a decision from status alone."
        ),
    )


class InvestigationListResponse(BaseModel):
    client_id: int
    external_client_id: int
    investigations: list[InvestigationRead] = []
    total: int


class AgentStatusResponse(BaseModel):
    provider: str
    model: str
    configured: bool
    prompt_version: str
    note: str = (
        "When configured is false the agent makes no call, and every investigation is recorded as "
        "FAILED with the reason. It never returns a fabricated or placeholder report."
    )


# ---------------------------------------------------------------------- #


def _int_list(raw: str | None) -> list[int]:
    value = _decode(raw)
    return [int(v) for v in value] if isinstance(value, list) else []


def _str_list(raw: str | None) -> list[str]:
    value = _decode(raw)
    return [str(v) for v in value] if isinstance(value, list) else []


def _decode(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _json_obj(raw: str | None) -> dict[str, Any] | None:
    value = _decode(raw)
    return value if isinstance(value, dict) else None


def _finding_read(finding) -> InvestigationFindingRead:
    return InvestigationFindingRead(
        id=finding.id,
        evidence_id=finding.evidence_id,
        finding_text=finding.finding_text,
        created_at=finding.created_at,
        finding_type=finding.finding_type,
        confidence_statement=finding.confidence_statement,
        grounding_status=finding.grounding_status,
        cited_evidence_ids=_int_list(finding.cited_evidence_ids_json),
        invalid_evidence_ids=_int_list(finding.invalid_evidence_ids_json),
    )


def investigation_read(investigation: Investigation) -> InvestigationRead:
    return InvestigationRead(
        id=investigation.id,
        client_id=investigation.client_id,
        status=investigation.status,
        trigger_snapshot_id=investigation.trigger_snapshot_id,
        opened_at=investigation.opened_at,
        closed_at=investigation.closed_at,
        summary=investigation.summary,
        findings=[_finding_read(f) for f in investigation.findings],
        trigger_reason=investigation.trigger_reason,
        triggering_alert_id=investigation.triggering_alert_id,
        error_message=investigation.error_message,
    )


def build_detail_response(investigation: Investigation) -> InvestigationDetailResponse:
    grounding = _json_obj(investigation.grounding_json)
    tokens_present = investigation.input_tokens is not None or investigation.output_tokens is not None

    return InvestigationDetailResponse(
        investigation=investigation_read(investigation),
        report=_json_obj(investigation.report_json),
        recommendations=[
            InvestigationRecommendationRead(
                id=r.id,
                action=r.action,
                rationale=r.rationale,
                cited_evidence_ids=_int_list(r.cited_evidence_ids_json),
            )
            for r in investigation.recommendations
        ],
        evaluation=InvestigationEvaluationRead(
            prompt_version=investigation.prompt_version,
            llm_provider=investigation.llm_provider,
            llm_model=investigation.llm_model,
            latency_ms=investigation.latency_ms,
            input_tokens=investigation.input_tokens,
            output_tokens=investigation.output_tokens,
            total_tokens=(
                (investigation.input_tokens or 0) + (investigation.output_tokens or 0)
                if tokens_present
                else None
            ),
            temperature=investigation.temperature,
            context_hash=investigation.context_hash,
            generated_at=investigation.generated_at,
            evidence_available_count=investigation.evidence_available_count,
            evidence_used_count=investigation.evidence_used_count,
            evidence_ignored_count=len(grounding.get("evidence_ignored", [])) if grounding else None,
            missing_information_count=len(grounding.get("missing_information", [])) if grounding else None,
            conflicting_evidence_count=grounding.get("conflicting_evidence_count") if grounding else None,
            grounding_passed=investigation.grounding_passed,
            hallucinated_citation_count=investigation.hallucinated_citation_count,
            ungrounded_finding_count=grounding.get("ungrounded_finding_count") if grounding else None,
            injection_flags=_str_list(investigation.injection_flags_json),
        ),
        grounding=grounding,
    )


__all__ = [
    "AgentStatusResponse",
    "InvestigationDetailResponse",
    "InvestigationEvaluationRead",
    "InvestigationFindingRead",
    "InvestigationListResponse",
    "InvestigationRead",
    "InvestigationRecommendationRead",
    "RunInvestigationRequest",
    "build_detail_response",
    "investigation_read",
]
