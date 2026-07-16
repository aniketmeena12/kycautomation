/**
 * Types mirroring the REAL backend contracts.
 *
 * Hand-written against the live OpenAPI schema rather than generated, and
 * deliberately NOT a wish-list: every field here exists on a response the
 * backend actually returns today. Where the UI wants something the API does not
 * serve (a per-customer risk band on the list endpoint, a global audit feed, a
 * risk-distribution aggregate), the type does not invent it -- the page shows an
 * honest empty state instead. See docs/phase-7-frontend.md SS Limitations.
 */

// ---------------------------------------------------------------- enums

export type RiskBand = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
export type SourceTier =
  | "TIER_1_AUTHORITATIVE"
  | "TIER_2_CURATED_DEMO"
  | "INTERNAL"
  | "EXTERNAL_LIVE"
export type ProviderResultStatus =
  | "SUCCESS"
  | "NO_RESULTS"
  | "NOT_CONFIGURED"
  | "RATE_LIMITED"
  | "TIMEOUT"
  | "ERROR"
export type CaseStatus = "OPEN" | "UNDER_REVIEW" | "ESCALATED" | "SAR_REVIEW" | "CLOSED"
export type AlertStatus = "OPEN" | "ACKNOWLEDGED" | "INVESTIGATING" | "CLOSED" | "FALSE_POSITIVE"
export type SARStatus = "DRAFT" | "SUBMITTED_FOR_REVIEW" | "APPROVED" | "REJECTED"
export type ActorType = "SYSTEM" | "AGENT" | "HUMAN"
export type GroundingStatus = "GROUNDED" | "UNGROUNDED" | "UNCITED"
export type SectorRisk = "Low" | "Medium" | "High"

/** The complete permitted vocabulary. APPROVE/REJECT of a *client* are absent
 *  by design on the backend (ADR-027) -- the UI must never offer them. */
export type ReviewAction =
  | "CONFIRM_MATCH"
  | "REJECT_MATCH"
  | "REQUEST_INFORMATION"
  | "CONTINUE_MONITORING"
  | "ESCALATE"
  | "APPROVE_DRAFT_SAR"
  | "REJECT_DRAFT_SAR"
  | "CLOSE_CASE"
  | "ACKNOWLEDGE"
  | "REQUEST_MORE_INFO"
  | "APPROVE"
  | "REJECT"

export type TimelineEntryType =
  | "MONITORING"
  | "PROVIDER_RESULT"
  | "ENTITY_RESOLUTION"
  | "EVIDENCE"
  | "RISK_EVENT"
  | "RISK_SCORE_CHANGE"
  | "ALERT"
  | "INVESTIGATION"
  | "HUMAN_REVIEW"
  | "SAR"

// ---------------------------------------------------------------- customers

export interface Client {
  id: number
  external_client_id: number
  client_name: string
  client_type: string
  sector: string
  sector_risk: SectorRisk
  country: string
  pep_flag: boolean
  sanctions_flag: boolean
  fatf_country_flag: boolean
  ofac_country_flag: boolean
  sectoral_sanctions_flag: boolean
  ownership_opacity_score: number
  source_dataset: string
  source_tier: SourceTier
  ingested_at: string
}

/**
 * `/customers` returns a BARE ARRAY (`response_model=list[ClientRead]`), with
 * NO total and a hard `limit` cap of 500. This surprised the first version of
 * this client, which assumed a `{clients, total}` wrapper like `/alerts` uses --
 * every count silently rendered as "--". The shape is asymmetric across the API;
 * do not assume a wrapper.
 */
export type ClientListResponse = Client[]

/** The server's hard cap on /customers?limit (Query(50, ge=1, le=500)). */
export const CUSTOMERS_MAX_LIMIT = 500

export interface Account {
  id: number
  external_account_id: string
  client_id: number
}

export interface TransactionSummary {
  client_id: number
  transaction_count: number
  total_amount: number
  flagged_count: number
  /** null (not 0) when the source carries no laundering label at all. The UI
   *  must render these differently: 0 means "checked, none found". */
  laundering_labelled_count: number | null
  earliest_transaction_at: string | null
  latest_transaction_at: string | null
}

export interface Evidence {
  id: number
  client_id: number | null
  evidence_type: string
  extracted_fact: string
  snippet: string | null
  confidence: number
  source_dataset: string
  source_tier: SourceTier
  provider_name: string | null
  producing_component: string
  retrieved_at: string | null
  created_at: string
}

export interface ProviderQuerySummary {
  provider_name: string
  provider_kind: string
  category: string
  status: ProviderResultStatus
  result_count: number
  error_message: string | null
}

export interface Customer360 {
  client: Client
  accounts: Account[]
  shallow_transaction_summary: TransactionSummary
  deep_transaction_summaries: unknown[]
  sanctions_candidates: unknown[]
  adverse_media_candidates: unknown[]
  ownership_note: string
  evidence: Evidence[]
  provider_availability: ProviderQuerySummary[]
  source_provenance: { source_dataset: string; source_tier: SourceTier; ingested_at: string }
  generated_at: string
}

// ---------------------------------------------------------------- risk

export interface RiskSnapshot {
  id: number
  client_id: number
  previous_score: number | null
  current_score: number
  risk_band: RiskBand
  previous_band: RiskBand | null
  delta: number | null
  computed_at: string
  trigger_reason: string | null
  scoring_logic_version: string | null
  factor_contributions: string | null
}

export interface CurrentRiskResponse {
  client_id: number
  external_client_id: number
  current: RiskSnapshot | null
  /** true when the client has never been scored. The UI must show this rather
   *  than defaulting to 0/LOW, which would assert "we assessed them and they're
   *  fine" when nobody ever looked. */
  never_monitored: boolean
}

export interface RiskHistoryResponse {
  client_id: number
  external_client_id: number
  snapshots: RiskSnapshot[]
  total: number
}

export interface RiskEvent {
  id: number
  client_id: number
  event_type: string
  severity: RiskBand
  confidence: number
  status: string
  detected_at: string
  event_timestamp: string | null
  summary: string | null
  source: string | null
  factor_id: string | null
}

export interface RiskEventListResponse {
  events: RiskEvent[]
  total: number
}

export interface RiskFactor {
  id: string
  name: string
  description: string
  category: string
  severity: RiskBand
  weight: number
  confidence_multiplier: number
  max_contribution: number
  requires_entity_resolution: boolean
  enabled: boolean
  event_type: string
}

export interface RiskFactorListResponse {
  factors: RiskFactor[]
  total: number
  enabled_count: number
  contribution_formula: string
  scoring_logic_version: string
  bands: Record<string, number>
}

/** Persisted on the snapshot as JSON; parsed client-side. */
export interface FactorContribution {
  factor_id: string
  factor_name: string
  category: string
  severity: RiskBand
  weight: number
  contribution: number
  reason: string
}

// ---------------------------------------------------------------- alerts

export interface Alert {
  id: number
  client_id: number
  status: AlertStatus
  severity: RiskBand
  trigger: string
  reason: string | null
  risk_delta: number | null
  dedup_key: string
  triggering_risk_event_id: number | null
  opened_at: string
  closed_at: string | null
}

export interface AlertListResponse {
  alerts: Alert[]
  total: number
}

// ---------------------------------------------------------------- investigations

export interface InvestigationFinding {
  id: number
  finding_text: string
  finding_type: "KEY_FINDING" | "SUPPORTING_EVIDENCE" | "CONFLICTING_EVIDENCE" | null
  evidence_id: number | null
  confidence_statement: string | null
  grounding_status: GroundingStatus | null
  cited_evidence_ids: number[]
  invalid_evidence_ids: number[]
  created_at: string
}

export interface InvestigationRecommendation {
  id: number
  action: string
  rationale: string
  cited_evidence_ids: number[]
}

export interface Investigation {
  id: number
  client_id: number
  status: string
  trigger_reason: string | null
  triggering_alert_id: number | null
  trigger_snapshot_id: number | null
  summary: string | null
  opened_at: string
  closed_at: string | null
  error_message: string | null
  findings: InvestigationFinding[]
}

export interface InvestigationEvaluation {
  prompt_version: string | null
  llm_provider: string | null
  llm_model: string | null
  latency_ms: number | null
  input_tokens: number | null
  output_tokens: number | null
  total_tokens: number | null
  temperature: number | null
  context_hash: string | null
  generated_at: string | null
  evidence_available_count: number | null
  evidence_used_count: number | null
  evidence_ignored_count: number | null
  missing_information_count: number | null
  conflicting_evidence_count: number | null
  grounding_passed: boolean | null
  hallucinated_citation_count: number | null
  ungrounded_finding_count: number | null
  injection_flags: string[]
}

export interface InvestigationReport {
  summary: string
  key_findings: { finding: string; evidence_ids: number[]; confidence_statement: string }[]
  supporting_evidence: { finding: string; evidence_ids: number[]; confidence_statement: string }[]
  conflicting_evidence: { finding: string; evidence_ids: number[]; confidence_statement: string }[]
  missing_information: string[]
  reasoning: string
  recommendations: { action: string; rationale: string; evidence_ids: number[] }[]
  confidence_statement: string
  limitations: string[]
  citations: number[]
}

export interface InvestigationDetail {
  investigation: Investigation
  report: InvestigationReport | null
  recommendations: InvestigationRecommendation[]
  evaluation: InvestigationEvaluation
  grounding: Record<string, unknown> | null
  human_review_required: boolean
}

export interface InvestigationListResponse {
  client_id: number
  external_client_id: number
  investigations: Investigation[]
  total: number
}

export interface AgentStatus {
  provider: string
  model: string
  configured: boolean
  prompt_version: string
  note: string
}

// ---------------------------------------------------------------- cases

export interface CaseSummary {
  id: number
  case_ref: string
  client_id: number
  external_client_id: number
  client_name: string
  status: CaseStatus
  title: string | null
  assigned_to: string | null
  opened_at: string
  closed_at: string | null
  current_risk_score: number | null
  current_risk_band: string | null
  open_alert_count: number
  investigation_count: number
  review_count: number
  has_sar_draft: boolean
}

export interface HumanReview {
  id: number
  reviewer_name: string
  action: ReviewAction
  comment: string | null
  decided_at: string
  previous_state: CaseStatus | null
  new_state: CaseStatus | null
  target_type: string | null
  target_id: number | null
}

export interface SARSection {
  key: string
  title: string
  body: string
  generated_by: string
  evidence_ids: number[]
}

export interface SARDraft {
  id: number
  sar_ref: string | null
  case_id: number | null
  client_id: number
  investigation_id: number | null
  status: SARStatus
  generated_at: string | null
  reviewed_by: string | null
  reviewed_at: string | null
  marking: string
  requires_human_approval: boolean
  sections: SARSection[]
  content: string | null
  cited_evidence_ids: number[]
  grounding_passed: boolean | null
  hallucinated_citation_count: number | null
  narrative_generated_by: string | null
  narrative_model: string | null
  prompt_version: string | null
  narrative_error: string | null
}

export interface ActionRequirement {
  action: ReviewAction
  /** If true, the review MUST carry target_id or the server rejects it. */
  requires_target: boolean
  /** Which record target_id names (SARDraft, EntityMatch). Null when not needed. */
  target_type: string | null
  description: string
}

export interface CaseDetail {
  case: CaseSummary
  available_actions: ReviewAction[]
  /**
   * The per-action contract for `available_actions`, from the backend's state
   * machine. Read this -- never reimplement it. The first version of this page
   * hardcoded which actions need a target_id by copying the backend's
   * `_ACTION_RULES` table by hand, missed APPROVE and REJECT, and so offered a
   * form with no target field that the server then rejected with
   * "Action APPROVE requires a target_id". The copy was the bug.
   *
   * Optional because a backend older than this field returns nothing; the page
   * falls back to asking for a target rather than assuming one isn't needed.
   */
  action_requirements?: ActionRequirement[]
  customer: Customer360 | null
  risk_current: { score: number; band: string; computed_at: string; explanation: string | null } | null
  risk_history: { id: number; score: number; band: string; delta: number | null; computed_at: string }[]
  risk_events: { id: number; type: string; severity: string; summary: string | null; detected_at: string; factor_id: string | null }[]
  entity_matches: { id: number; candidate_name: string; status: string; confidence: number; source_tier: string | null }[]
  evidence: Evidence[]
  alerts: { id: number; trigger: string; severity: string; status: string; reason: string | null; opened_at: string }[]
  investigations: { id: number; status: string; summary: string | null; grounding_passed: boolean | null; llm_model: string | null; opened_at: string; error_message: string | null }[]
  reviews: HumanReview[]
  sar_drafts: SARDraft[]
  human_decision_required: boolean
}

export interface CaseListResponse {
  cases: CaseSummary[]
  total: number
}

export interface CaseMetrics {
  open_cases: number
  under_review_cases: number
  escalated_cases: number
  sar_review_cases: number
  closed_cases: number
  total_cases: number
  high_risk_cases: number
  sar_pending: number
  sar_approved: number
  sar_rejected: number
  human_review_count: number
  human_reviews_by_action: Record<string, number>
  /** null (not 0) when no investigation has produced a report. 0.0 would read
   *  as "instant". */
  average_investigation_latency_ms: number | null
  investigations_total: number
  investigations_failed: number
  generated_at: string
}

export interface TimelineEntry {
  entry_key: string
  timestamp: string
  entry_type: TimelineEntryType
  title: string
  summary: string | null
  actor_type: ActorType
  actor_id: string | null
  related_entity: string | null
  related_evidence_ids: number[]
  related_event_id: number | null
  source_table: string
  source_id: number
  metadata: Record<string, unknown>
}

export interface CaseTimeline {
  case_id: number
  entries: TimelineEntry[]
  total: number
  generated_at: string
  counts_by_type: Record<string, number>
}

export interface AuditEntry {
  id: number
  created_at: string
  actor_type: ActorType
  actor_id: string | null
  action: string
  target_type: string | null
  target_id: string | null
  reason: string | null
  old_value: string | null
  new_value: string | null
  correlation_id: string | null
}

export interface CaseAuditResponse {
  case_id: number
  entries: AuditEntry[]
  total: number
  note: string
}

// ---------------------------------------------------------------- system

export interface ProviderInfo {
  provider_name: string
  provider_kind: string
  category: string
  configured: boolean
  description?: string | null
}

export interface ProviderListResponse {
  providers: ProviderInfo[]
  total: number
}

export interface DatasetStatus {
  source_key: string
  status: string
  records_loaded?: number | null
  last_ingested_at?: string | null
  message?: string | null
}

export interface DatasetStatusResponse {
  statuses: DatasetStatus[]
  total: number
}

export interface HealthCheck {
  name: string
  status: string
  detail: string | null
}

export interface HealthResponse {
  status: string
  checks: HealthCheck[]
}
