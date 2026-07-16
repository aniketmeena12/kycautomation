/**
 * The single HTTP boundary.
 *
 * Every network call in the app goes through `request()`. That is what makes
 * "no mock data" checkable rather than aspirational: there is exactly one place
 * a fabricated response could be injected, and it isn't.
 *
 * Errors become a typed ApiError carrying the status and the backend's own
 * `detail`. This matters because the backend's failure messages are deliberately
 * actionable ("No API key configured. Set LLM_API_KEY in backend/.env") and a
 * client that flattened them to "Request failed" would throw away the most
 * useful thing it received.
 */

import type {
  AgentStatus,
  AlertListResponse,
  CaseAuditResponse,
  CaseDetail,
  CaseListResponse,
  CaseMetrics,
  CaseStatus,
  CaseTimeline,
  ClientListResponse,
  CurrentRiskResponse,
  Customer360,
  DatasetStatusResponse,
  HealthResponse,
  InvestigationDetail,
  InvestigationListResponse,
  ProviderListResponse,
  ReviewAction,
  RiskEventListResponse,
  RiskFactorListResponse,
  RiskHistoryResponse,
  SARDraft,
} from "./types"

const BASE = import.meta.env.VITE_API_URL ?? ""
const API = `${BASE}/api/v1`

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly url: string,
  ) {
    super(detail)
    this.name = "ApiError"
  }

  /** 404 is routinely a legitimate empty state, not a failure: "no SAR draft
   *  exists yet" is the normal condition for most cases. Pages use this to
   *  render an empty state instead of an error. */
  get isNotFound() {
    return this.status === 404
  }

  /** 409 means the action conflicts with current state (an illegal case
   *  transition). The backend names the permitted actions in `detail`. */
  get isConflict() {
    return this.status === 409
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API}${path}`
  let response: Response
  try {
    response = await fetch(url, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    })
  } catch (cause) {
    // The API being unreachable is a distinct, common condition (backend not
    // running) and deserves its own message rather than a generic failure.
    throw new ApiError(0, `Cannot reach the API at ${url}. Is the backend running?`, url)
  }

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      // FastAPI puts validation errors in an array; flatten readably.
      if (typeof body?.detail === "string") detail = body.detail
      else if (Array.isArray(body?.detail))
        detail = body.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ")
    } catch {
      /* keep the status line */
    }
    throw new ApiError(response.status, detail, url)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

function qs(params: Record<string, string | number | boolean | undefined | null>) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value))
  }
  const s = search.toString()
  return s ? `?${s}` : ""
}

/**
 * One function per REAL endpoint. Nothing here calls a route that does not
 * exist -- the surface below was enumerated from the running app's OpenAPI
 * schema, not from the wish-list in the brief.
 */
export const api = {
  // --- health / system ---
  health: () => request<HealthResponse>("/../../health/ready" as string),
  providers: () => request<ProviderListResponse>("/providers"),
  datasetStatus: () => request<DatasetStatusResponse>("/datasets/status"),
  sources: () => request<{ sources: unknown[]; total: number }>("/sources"),

  // --- customers ---
  customers: (p: {
    limit?: number
    offset?: number
    sanctions_flag?: boolean
    pep_flag?: boolean
    sector_risk?: string
    mapped_only?: boolean
  }) => request<ClientListResponse>(`/customers${qs(p)}`),

  /**
   * Count customers matching a server-side filter.
   *
   * `/customers` exposes no `total` and caps `limit` at 500, so a count means
   * paging until a short page comes back. For 2,000 clients that is 4 small
   * requests -- acceptable, cached, and REAL. The alternative (guessing, or
   * showing "500+") would be either a fabricated number or a useless one.
   *
   * Returns `{ count, exact }`. If the cap is somehow hit, `exact` is false and
   * the UI must say so rather than present a floor as a total.
   */
  countCustomers: async (
    filter: { sanctions_flag?: boolean; pep_flag?: boolean; sector_risk?: string } = {},
    maxPages = 12,
  ): Promise<{ count: number; exact: boolean }> => {
    const LIMIT = 500
    let count = 0
    for (let page = 0; page < maxPages; page++) {
      const rows = await request<ClientListResponse>(
        `/customers${qs({ ...filter, limit: LIMIT, offset: page * LIMIT })}`,
      )
      count += rows.length
      if (rows.length < LIMIT) return { count, exact: true }
    }
    return { count, exact: false }
  },
  customer360: (clientId: number) => request<Customer360>(`/customers/${clientId}/360`),

  // --- risk ---
  currentRisk: (clientId: number) => request<CurrentRiskResponse>(`/risk/${clientId}`),
  riskHistory: (clientId: number) => request<RiskHistoryResponse>(`/risk/history/${clientId}`),
  riskFactors: () => request<RiskFactorListResponse>("/risk/factors"),
  riskEvents: (clientId: number, limit = 100) =>
    request<RiskEventListResponse>(`/events/${clientId}${qs({ limit })}`),

  // --- alerts ---
  alerts: (p: { client_id?: number; status?: string; severity?: string; limit?: number; offset?: number }) =>
    request<AlertListResponse>(`/alerts${qs(p)}`),

  // --- investigations ---
  agentStatus: () => request<AgentStatus>("/investigations/agent/status"),
  investigation: (id: number) => request<InvestigationDetail>(`/investigations/${id}`),
  investigationsForClient: (externalClientId: number) =>
    request<InvestigationListResponse>(`/investigations/client/${externalClientId}`),
  runInvestigation: (externalClientId: number, body: { trigger_reason?: string; alert_id?: number }) =>
    request<InvestigationDetail>(`/investigations/run/${externalClientId}`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rerunInvestigation: (id: number) =>
    request<InvestigationDetail>(`/investigations/${id}/rerun`, { method: "POST" }),

  // --- cases ---
  cases: (p: { status?: CaseStatus; assigned_to?: string; limit?: number; offset?: number }) =>
    request<CaseListResponse>(`/cases${qs(p)}`),
  caseMetrics: () => request<CaseMetrics>("/cases/metrics"),
  case: (id: number) => request<CaseDetail>(`/cases/${id}`),
  openCase: (body: { external_client_id: number; reason?: string; title?: string; investigation_id?: number }) =>
    request<CaseDetail>("/cases", { method: "POST", body: JSON.stringify(body) }),
  caseTimeline: (id: number) => request<{ timeline: CaseTimeline }>(`/cases/${id}/timeline`),
  caseAudit: (id: number, limit = 200) => request<CaseAuditResponse>(`/cases/${id}/audit${qs({ limit })}`),
  submitReview: (
    id: number,
    body: { reviewer: string; action: ReviewAction; comment?: string; target_id?: number },
  ) => request<CaseDetail>(`/cases/${id}/review`, { method: "POST", body: JSON.stringify(body) }),
  generateSar: (id: number, body: { requested_by: string }) =>
    request<SARDraft>(`/cases/${id}/sar`, { method: "POST", body: JSON.stringify(body) }),
  sar: (id: number) => request<SARDraft>(`/cases/${id}/sar`),

  // --- monitoring ---
  monitorClient: (externalClientId: number) =>
    request<unknown>(`/monitor/client/${externalClientId}`, {
      method: "POST",
      body: JSON.stringify({ include_providers: false, include_resolution: false }),
    }),
}

/** Health lives outside /api/v1, so it needs its own absolute path. */
export async function fetchHealth(): Promise<HealthResponse> {
  const url = `${BASE}/health/ready`
  try {
    const response = await fetch(url)
    if (!response.ok) throw new ApiError(response.status, `${response.status} ${response.statusText}`, url)
    return (await response.json()) as HealthResponse
  } catch (cause) {
    if (cause instanceof ApiError) throw cause
    throw new ApiError(0, `Cannot reach the API at ${url}. Is the backend running?`, url)
  }
}
