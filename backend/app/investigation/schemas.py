"""
Investigation contracts: the grounded context that goes IN, and the structured
report that must come OUT.

THE CENTRAL INVARIANT
---------------------
`InvestigationContext` is the agent's entire world. The agent has no database
session, no provider registry, and no tools -- it cannot look anything up. If a
fact is not in the context, the model has no legitimate way to know it, which
is what makes the grounding check in app/investigation/grounding.py meaningful:
any evidence id in the report that is not in `allowed_evidence_ids` was
invented, with no ambiguity about whether it "came from somewhere else".

That is also why context assembly (app/investigation/context.py) reads only
from the database and never fabricates a placeholder. An investigation of a
client with two pieces of evidence gets a context with two pieces of evidence,
and the report has to live with that.

EVERY EVIDENCE ITEM CARRIES ITS PROVENANCE INTO THE PROMPT
----------------------------------------------------------
`source_tier` travels with each item and is rendered for the model. Phase 0 SS3
found that client names match 0/2000 against the authoritative lists, so
essentially all sanctions-flavoured evidence here is TIER_2_CURATED_DEMO or an
upstream label this system did not derive. An agent shown a hit without its
tier would reasonably narrate it as an authoritative sanctions match. Carrying
the tier is how ADR-002 ("the two tiers are never merged") survives contact
with a component that writes prose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import (
    EvidenceType,
    InvestigationRecommendationAction,
    ProviderResultStatus,
    RiskBand,
    SourceTier,
)

# ---------------------------------------------------------------------- #
# Context (what the agent is allowed to know)
# ---------------------------------------------------------------------- #


class ContextEvidenceItem(BaseModel):
    """One citable fact. `evidence_id` is the real Evidence row id -- the token
    the model must cite and the token we deterministically verify."""

    evidence_id: int
    evidence_type: EvidenceType
    summary: str
    confidence: float
    source_dataset: str
    source_tier: SourceTier
    provider_name: str | None = None
    retrieved_at: datetime | None = None
    structured_facts: dict[str, Any] | None = None
    # Verbatim third-party text (e.g. a news article body). UNTRUSTED. Rendered
    # inside a quarantine block and never treated as instructions -- see
    # app/investigation/prompts.py.
    snippet: str | None = None


class ContextRiskEvent(BaseModel):
    event_id: int
    event_type: str
    severity: RiskBand
    confidence: float
    summary: str | None = None
    source: str | None = None
    detected_at: datetime
    factor_id: str | None = None


class ContextEntityMatch(BaseModel):
    match_id: int
    subject_ref: str
    candidate_name: str | None = None
    candidate_provider: str | None = None
    source_tier: SourceTier | None = None
    confidence: float
    status: str
    matched_attributes: list[str] = Field(default_factory=list)
    conflicting_attributes: list[str] = Field(default_factory=list)


class ContextAlert(BaseModel):
    alert_id: int
    trigger: str
    severity: RiskBand
    reason: str | None = None
    risk_delta: float | None = None
    opened_at: datetime


class ContextRiskAssessment(BaseModel):
    """The deterministic engine's output, handed to the agent as a FACT to
    explain -- never as a number to reproduce, adjust, or second-guess.

    This field is the core design principle made concrete: the score arrives
    already computed by app/risk/engine.py, and the agent's job is narration.
    """

    score: float
    band: RiskBand
    previous_score: float | None = None
    previous_band: RiskBand | None = None
    delta: float | None = None
    computed_at: datetime
    explanation: str | None = None
    factor_contributions: list[dict[str, Any]] = Field(default_factory=list)
    scoring_logic_version: str | None = None


class ContextProviderResult(BaseModel):
    """Coverage, not content. 'We queried X and it was unavailable' is a fact
    the report must be able to state -- it is the difference between "no
    adverse media exists" and "we could not check for adverse media"."""

    provider_name: str
    category: str | None = None
    status: ProviderResultStatus
    detail: str | None = None
    queried_at: datetime | None = None


class ContextTransactionSummary(BaseModel):
    """Mirrors TransactionRepository.summary_for_client exactly.

    `laundering_labelled_count` is None -- not 0 -- when the underlying source
    carries no laundering label at all. 0 would tell the agent "we checked and
    found none"; None says "there was nothing to check". Collapsing the two
    would let an absent column read as a clean result.
    """

    transaction_count: int = 0
    total_amount: float = 0.0
    flagged_count: int = 0
    laundering_labelled_count: int | None = None
    earliest_transaction_at: datetime | None = None
    latest_transaction_at: datetime | None = None


class ContextOwnershipNode(BaseModel):
    entity_ref: str
    name: str
    entity_type: str | None = None
    ownership_percentage: float | None = None
    jurisdiction: str | None = None
    is_ubo: bool = False


class ContextClient(BaseModel):
    """Field-for-field what the Client model actually stores.

    The flag names are kept verbatim from the source (`fatf_country_flag`,
    `ofac_country_flag`) rather than renamed to something friendlier like
    `fatf_high_risk`: a renamed flag is a flag whose meaning has quietly been
    reinterpreted by whoever chose the new name, and the prompt renders these
    to a model that will narrate them.
    """

    external_client_id: int
    client_name: str
    client_type: str | None = None
    country: str | None = None
    sector: str | None = None
    sector_risk: str | None = None
    sanctions_flag: bool = False
    pep_flag: bool = False
    fatf_country_flag: bool = False
    ofac_country_flag: bool = False
    sectoral_sanctions_flag: bool = False
    ownership_opacity_score: float | None = None
    source_dataset: str | None = None
    source_tier: SourceTier | None = None


class InvestigationContext(BaseModel):
    """Everything the agent may know, and nothing else."""

    client: ContextClient
    trigger_reason: str
    risk_assessment: ContextRiskAssessment | None = None
    risk_events: list[ContextRiskEvent] = Field(default_factory=list)
    entity_matches: list[ContextEntityMatch] = Field(default_factory=list)
    alerts: list[ContextAlert] = Field(default_factory=list)
    evidence: list[ContextEvidenceItem] = Field(default_factory=list)
    provider_results: list[ContextProviderResult] = Field(default_factory=list)
    transaction_summary: ContextTransactionSummary | None = None
    ownership: list[ContextOwnershipNode] = Field(default_factory=list)
    account_count: int = 0

    assembled_at: datetime
    # Deterministic notes about the context itself: what was empty, what was
    # truncated, what looked like an injection attempt. Rendered to the model
    # AND persisted, so a thin report is explainable after the fact.
    context_notes: list[str] = Field(default_factory=list)
    # Set when untrusted text contained imperative patterns aimed at the model
    # (app/investigation/grounding.py::scan_for_injection).
    injection_flags: list[str] = Field(default_factory=list)

    @property
    def allowed_evidence_ids(self) -> set[int]:
        """The complete citable universe. The grounding validator's allowlist."""
        return {item.evidence_id for item in self.evidence}


# ---------------------------------------------------------------------- #
# Report (what the agent must produce)
# ---------------------------------------------------------------------- #


class ReportFinding(BaseModel):
    finding: str
    evidence_ids: list[int] = Field(default_factory=list)
    confidence_statement: str = ""


class ReportRecommendation(BaseModel):
    """`action` is enum-typed, so a recommendation outside the permitted
    vocabulary cannot even be represented. See InvestigationRecommendationAction
    -- APPROVE/REJECT are absent by design."""

    action: InvestigationRecommendationAction
    rationale: str
    evidence_ids: list[int] = Field(default_factory=list)


class InvestigationReport(BaseModel):
    """The agent's structured output (Phase 5 brief SS6), validated against
    JSON_SCHEMA below before it is ever persisted or shown.

    Note `reasoning`: this is the report's ANALYTICAL RATIONALE -- a deliberate,
    reader-facing section explaining how the cited evidence supports the
    conclusions. It is NOT chain-of-thought. The model's internal reasoning is
    never requested and never received (see ADR-026); this field is an authored
    output, in the same sense a human analyst's written rationale is not a
    transcript of their thoughts. Conflating the two would mean either storing
    CoT (banned) or shipping an unexplained report (useless).
    """

    # The prose fields are REQUIRED, matching JSON_SCHEMA["required"]. Giving
    # them ""-defaults would make this gate weaker than the one before it: a
    # provider whose constrained-output mode silently dropped `reasoning` would
    # sail through Pydantic and produce a report with no rationale at all.
    # Gate 2 exists to catch exactly that, so it must not be more permissive
    # than gate 1.
    summary: str
    reasoning: str
    confidence_statement: str

    # The collections default to empty because empty is a MEANINGFUL, correct
    # value here, not a missing one: "the evidence supports no key findings" and
    # "there is no conflicting evidence" are legitimate -- and on a thin
    # evidence base, desirable -- outcomes. Forcing them to be non-empty would
    # pressure the model to invent content to satisfy a validator, which is the
    # precise failure this phase is built to prevent.
    key_findings: list[ReportFinding] = Field(default_factory=list)
    supporting_evidence: list[ReportFinding] = Field(default_factory=list)
    conflicting_evidence: list[ReportFinding] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    recommendations: list[ReportRecommendation] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    citations: list[int] = Field(default_factory=list)


def _finding_array(description: str) -> dict[str, Any]:
    return {
        "type": "array",
        "description": description,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["finding", "evidence_ids", "confidence_statement"],
            "properties": {
                "finding": {
                    "type": "string",
                    "description": "One specific, self-contained statement.",
                },
                "evidence_ids": {
                    "type": "array",
                    "description": (
                        "evidence_id values from the provided context that support this "
                        "statement. Every id MUST appear in the context. Never invent an id. "
                        "If nothing in the context supports the statement, do not make it."
                    ),
                    "items": {"type": "integer"},
                },
                "confidence_statement": {
                    "type": "string",
                    "description": "How strongly the cited evidence supports this, and why.",
                },
            },
        },
    }


# The wire schema handed to the provider's constrained-output mode.
#
# Constraint-shape notes (these are real API limits, not style choices):
#   * every object needs additionalProperties: false and an explicit `required`;
#   * minLength/minItems/maximum and similar are NOT supported and are silently
#     rejected -- semantic rules like "cite something" therefore have to be
#     enforced by app/investigation/grounding.py, in code, after the fact;
#   * `enum` IS supported, which is why the recommendation vocabulary can be
#     locked at the API boundary rather than merely asked for in the prompt.
JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "key_findings",
        "supporting_evidence",
        "conflicting_evidence",
        "missing_information",
        "reasoning",
        "recommendations",
        "confidence_statement",
        "limitations",
        "citations",
    ],
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "A concise narrative summary of what the evidence shows about this client. "
                "State only what the provided evidence supports. If the evidence is thin, "
                "say so plainly rather than padding."
            ),
        },
        "key_findings": _finding_array(
            "The most important conclusions the evidence supports. Empty if the evidence supports none."
        ),
        "supporting_evidence": _finding_array(
            "Evidence corroborating the assessed risk, each tied to its evidence_ids."
        ),
        "conflicting_evidence": _finding_array(
            "Evidence that CONTRADICTS or weakens the assessed risk, or that suggests a false "
            "positive. Report this as diligently as supporting evidence -- omitting exculpatory "
            "evidence is a defect, not a convenience."
        ),
        "missing_information": {
            "type": "array",
            "description": (
                "Specific information a reviewer would need that is absent from the context "
                "(e.g. an unqueried provider, an unavailable document). Do not guess at its content."
            ),
            "items": {"type": "string"},
        },
        "reasoning": {
            "type": "string",
            "description": (
                "The analytical rationale connecting the cited evidence to the summary and "
                "findings, written for a compliance reviewer."
            ),
        },
        "recommendations": {
            "type": "array",
            "description": (
                "Recommended next steps. You may only recommend from the listed actions. "
                "You must NEVER recommend final approval or rejection of the client -- that "
                "decision belongs to a human reviewer and is not yours to make."
            ),
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "rationale", "evidence_ids"],
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [a.value for a in InvestigationRecommendationAction],
                    },
                    "rationale": {"type": "string"},
                    "evidence_ids": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
        "confidence_statement": {
            "type": "string",
            "description": (
                "Your overall confidence in this investigation and what drives it. Do not output "
                "a numeric risk score or a numeric confidence value -- those are computed "
                "deterministically elsewhere and are not yours to assign."
            ),
        },
        "limitations": {
            "type": "array",
            "description": (
                "What this investigation could NOT establish, and any caveats about the "
                "evidence's provenance (e.g. reliance on curated demo data)."
            ),
            "items": {"type": "string"},
        },
        "citations": {
            "type": "array",
            "description": "Every evidence_id referenced anywhere in this report.",
            "items": {"type": "integer"},
        },
    },
}


__all__ = [
    "ContextAlert",
    "ContextClient",
    "ContextEntityMatch",
    "ContextEvidenceItem",
    "ContextOwnershipNode",
    "ContextProviderResult",
    "ContextRiskAssessment",
    "ContextRiskEvent",
    "ContextTransactionSummary",
    "InvestigationContext",
    "InvestigationReport",
    "ReportFinding",
    "ReportRecommendation",
    "JSON_SCHEMA",
]
