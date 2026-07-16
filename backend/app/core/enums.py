"""
Controlled-vocabulary enums shared across models, schemas, and services.

Every enum here backs a real column or API field introduced in Phase 1. Fields
that are genuinely open-ended in the Phase 0 data (e.g. free-text sanctions
`entity_type`, which differs between OFAC's individual/vessel/aircraft
vocabulary and OpenSanctions' Person/Company/LegalEntity/... `schema` field)
are deliberately left as plain strings rather than forced into an enum -- see
docs/data-dictionary.md for why those vocabularies aren't compatible.
"""

from enum import Enum


class SourceTier(str, Enum):
    """Provenance tier for any ingested record. See docs/phase-0-dataset-audit.md SS4.5.

    TIER_1_AUTHORITATIVE: real, full-scale reference data (OFAC SDN, OpenSanctions).
    TIER_2_CURATED_DEMO: the small, deliberately-curated sanctions/media/UBO fixture
        set. Valid for the demo, but must NEVER be presented as authoritative.
    INTERNAL: the project's own operational KYC dataset (clients, accounts,
        transactions) -- not a sanctions/watchlist source at all.
    """

    TIER_1_AUTHORITATIVE = "TIER_1_AUTHORITATIVE"
    TIER_2_CURATED_DEMO = "TIER_2_CURATED_DEMO"
    INTERNAL = "INTERNAL"
    EXTERNAL_LIVE = "EXTERNAL_LIVE"  # data retrieved at runtime from a configured external API


class ProviderKind(str, Enum):
    """Classifies a *data provider* (as opposed to SourceTier, which classifies
    a *record*). A provider is INTERNAL_DATASET (this project's own KYC data),
    LOCAL_REFERENCE_DATASET (a file on disk used as a lookup/fallback, e.g.
    the Tier-2 curated sanctions fixture), or EXTERNAL_API (a live, network-
    backed integration configured via environment variables). See
    app/providers/ -- this is the foundation for the hybrid internal + local +
    live-external architecture described in docs/phase-1-foundation.md."""

    INTERNAL_DATASET = "INTERNAL_DATASET"
    LOCAL_REFERENCE_DATASET = "LOCAL_REFERENCE_DATASET"
    EXTERNAL_API = "EXTERNAL_API"


class ProviderCategory(str, Enum):
    SANCTIONS = "SANCTIONS"
    ADVERSE_MEDIA = "ADVERSE_MEDIA"
    CORPORATE_REGISTRY = "CORPORATE_REGISTRY"
    TRANSACTION = "TRANSACTION"
    OWNERSHIP = "OWNERSHIP"


class ProviderResultStatus(str, Enum):
    """Outcome of a single provider query. Every provider call returns one of
    these -- callers must never assume SUCCESS, and a non-SUCCESS status must
    never raise an unhandled exception that could crash the application."""

    SUCCESS = "SUCCESS"
    NO_RESULTS = "NO_RESULTS"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"


class SourceType(str, Enum):
    """Which underlying dataset/feed a record came from."""

    OFAC_SDN = "OFAC_SDN"
    OPENSANCTIONS = "OPENSANCTIONS"
    CURATED_OFAC = "CURATED_OFAC"
    CURATED_OPENSANCTIONS = "CURATED_OPENSANCTIONS"
    INTERNAL_KYC = "INTERNAL_KYC"
    ADVERSE_MEDIA_FIXTURE = "ADVERSE_MEDIA_FIXTURE"
    UBO_GRAPH_FIXTURE = "UBO_GRAPH_FIXTURE"


class SourceCategory(str, Enum):
    """What kind of data a registry source provides."""

    CLIENT_MASTER = "CLIENT_MASTER"
    ACCOUNT_MAPPING = "ACCOUNT_MAPPING"
    TRANSACTION = "TRANSACTION"
    SANCTIONS_LIST = "SANCTIONS_LIST"
    WATCHLIST = "WATCHLIST"
    ADVERSE_MEDIA = "ADVERSE_MEDIA"
    OWNERSHIP_GRAPH = "OWNERSHIP_GRAPH"


class SourceFormat(str, Enum):
    CSV = "CSV"
    JSON = "JSON"
    TEXT = "TEXT"


class IngestionStrategy(str, Enum):
    """How a registered source is meant to be loaded in a future phase.

    Phase 1 does not implement any of these strategies -- it only records
    which one applies, per docs/system-design-phase-0.md.
    """

    FULL_LOAD = "FULL_LOAD"
    CHUNKED_LOAD = "CHUNKED_LOAD"
    STREAM = "STREAM"
    LOOKUP_ONLY = "LOOKUP_ONLY"
    CURATED_FIXTURE = "CURATED_FIXTURE"


class IngestionStatus(str, Enum):
    """Runtime status tracked in DatasetSourceStatus, distinct from the static
    registry metadata. NOT_INGESTED is the default for every source until a
    future ingestion job runs; Phase 1's validation pass can only reach
    VALIDATED or VALIDATION_FAILED -- never LOADED."""

    NOT_INGESTED = "NOT_INGESTED"
    VALIDATED = "VALIDATED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    PARTIALLY_LOADED = "PARTIALLY_LOADED"
    LOADED = "LOADED"


class ActorType(str, Enum):
    SYSTEM = "SYSTEM"
    AGENT = "AGENT"
    HUMAN = "HUMAN"


class ClientType(str, Enum):
    """Mirrors the exact values observed in clients_with_fatf_ofac.csv."""

    NGO = "NGO"
    FINANCIAL_INSTITUTION = "Financial Institution"
    CORPORATE = "Corporate"
    INDIVIDUAL = "Individual"


class SectorRisk(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class TransactionSourceType(str, Enum):
    """Which of the two non-overlapping transaction datasets a row came from.
    See docs/data-dictionary.md -- these carry different flag sets and must
    not be forced into one column layout."""

    SHALLOW_KYC_TXN = "SHALLOW_KYC_TXN"  # transactions_with_fatf_ofac.csv
    SAML_D = "SAML_D"  # aml_transactions/SAML-D.csv


class EvidenceType(str, Enum):
    SANCTIONS_MATCH = "SANCTIONS_MATCH"
    ADVERSE_MEDIA = "ADVERSE_MEDIA"
    TRANSACTION_TYPOLOGY = "TRANSACTION_TYPOLOGY"
    UBO_EXPOSURE = "UBO_EXPOSURE"
    LABELLED_LAUNDERING = "LABELLED_LAUNDERING"
    PEP_EXPOSURE = "PEP_EXPOSURE"
    # Phase 3 additions (brief SS10): a raw provider response recorded as
    # evidence in its own right, and evidence entered by a human reviewer.
    PROVIDER_RESPONSE = "PROVIDER_RESPONSE"
    MANUAL = "MANUAL"
    OTHER = "OTHER"


class RiskEventType(str, Enum):
    SANCTIONS_MATCH = "SANCTIONS_MATCH"
    ADVERSE_MEDIA_HIT = "ADVERSE_MEDIA_HIT"
    TRANSACTION_TYPOLOGY = "TRANSACTION_TYPOLOGY"
    UBO_EXPOSURE = "UBO_EXPOSURE"
    LABELLED_LAUNDERING = "LABELLED_LAUNDERING"
    SCORE_CHANGE = "SCORE_CHANGE"
    PROMPT_INJECTION_ATTEMPT = "PROMPT_INJECTION_ATTEMPT"
    # --- Phase 4 additions (brief SS3) ---
    SANCTIONS_CANDIDATE = "SANCTIONS_CANDIDATE"
    HIGH_CONFIDENCE_MATCH = "HIGH_CONFIDENCE_MATCH"
    HIGH_RISK_GEOGRAPHY = "HIGH_RISK_GEOGRAPHY"
    WATCHLIST_MATCH = "WATCHLIST_MATCH"
    PEP_EXPOSURE = "PEP_EXPOSURE"
    # A provider being unavailable is itself a monitoring fact: it means the
    # cycle's coverage was incomplete. Recording it as an event is what stops
    # "we found nothing" and "we couldn't look" from being indistinguishable.
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    # Entity resolution surfaced contradicting attributes on a candidate.
    ENTITY_CONFLICT = "ENTITY_CONFLICT"
    OTHER = "OTHER"


class AlertTrigger(str, Enum):
    """Why an alert fired. Distinct from the alert's severity."""

    BAND_ESCALATION = "BAND_ESCALATION"
    SCORE_DELTA = "SCORE_DELTA"
    CRITICAL_EVENT = "CRITICAL_EVENT"
    REPEATED_SIGNAL = "REPEATED_SIGNAL"
    PROVIDER_DEGRADED = "PROVIDER_DEGRADED"


class RiskEventStatus(str, Enum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class RiskBand(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class InvestigationStatus(str, Enum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_HUMAN_REVIEW = "AWAITING_HUMAN_REVIEW"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"
    # Phase 5: the agent ran but produced no usable report (provider not
    # configured, timed out, or returned output that failed validation). A
    # distinct terminal state, deliberately NOT reusing CLOSED -- "we
    # investigated and closed it" and "we could not investigate" are opposite
    # facts, and collapsing them would let a coverage gap read as a clean
    # result. Only a machine sets this; ESCALATED/CLOSED stay human-only.
    FAILED = "FAILED"


class InvestigationRecommendationAction(str, Enum):
    """The complete set of actions the Investigation Agent may recommend
    (Phase 5 brief SS8).

    APPROVE and REJECT are absent BY DESIGN, not by omission. The agent
    recommends; a human decides. This enum is emitted into the model's JSON
    schema as an `enum` constraint, so the structured-output layer itself
    rejects an out-of-vocabulary action -- the boundary is enforced by the API
    contract, not merely requested in the prompt. A deterministic re-check in
    app/investigation/grounding.py backs it up in case the schema is ever
    relaxed. See ADR-027.
    """

    CONTINUE_MONITORING = "CONTINUE_MONITORING"
    REQUEST_DOCUMENTATION = "REQUEST_DOCUMENTATION"
    ENHANCED_DUE_DILIGENCE = "ENHANCED_DUE_DILIGENCE"
    ESCALATE = "ESCALATE"
    DRAFT_SAR_REVIEW = "DRAFT_SAR_REVIEW"
    CLOSE_INVESTIGATION = "CLOSE_INVESTIGATION"


class InvestigationFindingType(str, Enum):
    """Which section of the agent's report a persisted finding came from."""

    KEY_FINDING = "KEY_FINDING"
    SUPPORTING_EVIDENCE = "SUPPORTING_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"


class GroundingStatus(str, Enum):
    """Result of deterministically checking one finding's citations against the
    evidence that was actually in the assembled context.

    GROUNDED    -- every cited evidence id was in the context.
    UNGROUNDED  -- cited at least one id that was NOT in the context. The
                   model referred to evidence that does not exist: a
                   hallucinated citation.
    UNCITED     -- cited nothing at all.

    Non-GROUNDED findings are still persisted verbatim, flagged. Deleting them
    would hide the fact that the model hallucinated, which is exactly what a
    reviewer most needs to know.
    """

    GROUNDED = "GROUNDED"
    UNGROUNDED = "UNGROUNDED"
    UNCITED = "UNCITED"


class AlertStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    INVESTIGATING = "INVESTIGATING"
    CLOSED = "CLOSED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class ReviewAction(str, Enum):
    """What a human reviewer did.

    The first five are Phase 1's original vocabulary, kept verbatim rather than
    renamed -- existing rows and queries stay valid, the same additive
    discipline Phase 4 and 5 used when extending models.

    The Phase 6 additions are deliberately SPECIFIC where the originals were
    generic. `APPROVE` alone cannot express *what* was approved, and in a
    compliance file "approved" without an object is the kind of ambiguity that
    makes an audit trail useless -- APPROVE_DRAFT_SAR and CONFIRM_MATCH are
    different decisions with different consequences and different authority.
    """

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"
    REQUEST_MORE_INFO = "REQUEST_MORE_INFO"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    # --- Phase 6 (brief SS4) ---
    CONFIRM_MATCH = "CONFIRM_MATCH"
    REJECT_MATCH = "REJECT_MATCH"
    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    CONTINUE_MONITORING = "CONTINUE_MONITORING"
    APPROVE_DRAFT_SAR = "APPROVE_DRAFT_SAR"
    REJECT_DRAFT_SAR = "REJECT_DRAFT_SAR"
    CLOSE_CASE = "CLOSE_CASE"


class CaseStatus(str, Enum):
    """The compliance case lifecycle (Phase 6 brief SS5).

    Every transition is validated by app/casework/state_machine.py; an illegal
    one raises rather than silently succeeding. CLOSED is terminal by design --
    reopening a closed compliance case would erase the fact that it was closed,
    and the honest way to revisit one is a new case referencing it.
    """

    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    ESCALATED = "ESCALATED"
    SAR_REVIEW = "SAR_REVIEW"
    CLOSED = "CLOSED"


class TimelineEntryType(str, Enum):
    """Where a timeline entry was DERIVED from.

    Each value maps to a stored table, never to a hand-written narrative step:
    the timeline is generated from what happened, so an entry type is really a
    statement about provenance (brief SS3: "Never manually assemble timelines").
    """

    MONITORING = "MONITORING"
    PROVIDER_RESULT = "PROVIDER_RESULT"
    ENTITY_RESOLUTION = "ENTITY_RESOLUTION"
    EVIDENCE = "EVIDENCE"
    RISK_EVENT = "RISK_EVENT"
    RISK_SCORE_CHANGE = "RISK_SCORE_CHANGE"
    ALERT = "ALERT"
    INVESTIGATION = "INVESTIGATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    SAR = "SAR"


class SARStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED_FOR_REVIEW = "SUBMITTED_FOR_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EntityMatchSubjectType(str, Enum):
    """What kind of record an EntityMatch candidate is being resolved against.
    Not a real foreign key (a match can point at any of several tables), so
    subject_id is a plain integer discriminated by this field -- a documented
    lightweight polymorphic-association pattern, not an oversight."""

    CLIENT = "CLIENT"
    OWNERSHIP_ENTITY = "OWNERSHIP_ENTITY"
    ADVERSE_MEDIA_MENTION = "ADVERSE_MEDIA_MENTION"


class EntityMatchStatus(str, Enum):
    """Lifecycle of a resolution candidate.

    The Phase 3 entity-resolution engine can only ever produce the first four
    (see app/resolution/confidence.py::status_for):

      CANDIDATE       -- plausible, but weakly corroborated.
      POSSIBLE        -- meaningful corroboration; needs a human look.
      HIGH_CONFIDENCE -- strong corroboration. Still NOT "confirmed".
      AUTO_REJECTED   -- the engine's "Rejected" state (Phase 3 brief SS9).
                         Named AUTO_REJECTED since Phase 1 to make clear the
                         machine rejected it, not a person; kept rather than
                         renamed so existing rows/queries stay valid.

    CONFIRMED and HUMAN_REVIEWED are reserved for a human acting in a later
    phase. No automated code path may set them -- enforced by test.
    """

    CANDIDATE = "CANDIDATE"
    POSSIBLE = "POSSIBLE"
    HIGH_CONFIDENCE = "HIGH_CONFIDENCE"
    AUTO_REJECTED = "AUTO_REJECTED"
    CONFIRMED = "CONFIRMED"
    HUMAN_REVIEWED = "HUMAN_REVIEWED"
