/**
 * TanStack Query hooks -- one per real endpoint.
 *
 * Caching policy is deliberate, not default:
 *
 *  - Reference data that changes only on redeploy (risk factors, providers,
 *    sources) is effectively immutable within a session -> long staleTime.
 *  - Compliance state (cases, alerts, investigations) is refetched on mount but
 *    NOT on window focus. A reviewer tabbing back to a case must not have the
 *    page shift under them mid-decision; a silent refetch that reorders a queue
 *    while someone is clicking it is worse than slightly stale data.
 *  - Nothing polls. This system has no live feed, and a 5s interval against a
 *    single-writer SQLite backend (ADR-001) would be load with no signal.
 *
 * 404 is never retried: across this API it routinely means a legitimate empty
 * state ("no SAR draft yet"), not a transient failure.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ApiError, api, fetchHealth } from "@/api/client"
import type { CaseStatus, ReviewAction } from "@/api/types"

const MINUTE = 60_000

/** Reference data: changes only when the operator edits config and redeploys. */
const REFERENCE = { staleTime: 30 * MINUTE, gcTime: 60 * MINUTE }
/** Operational data: fresh on mount, stable while you work. */
const OPERATIONAL = { staleTime: 30_000, refetchOnWindowFocus: false as const }

export const keys = {
  health: ["health"] as const,
  providers: ["providers"] as const,
  datasets: ["datasets"] as const,
  sources: ["sources"] as const,
  riskFactors: ["risk", "factors"] as const,
  agentStatus: ["agent", "status"] as const,
  caseMetrics: ["cases", "metrics"] as const,
  customers: (p: unknown) => ["customers", p] as const,
  customer360: (id: number) => ["customers", id, "360"] as const,
  currentRisk: (id: number) => ["risk", id] as const,
  riskHistory: (id: number) => ["risk", "history", id] as const,
  riskEvents: (id: number) => ["events", id] as const,
  alerts: (p: unknown) => ["alerts", p] as const,
  cases: (p: unknown) => ["cases", p] as const,
  case: (id: number) => ["cases", id] as const,
  caseTimeline: (id: number) => ["cases", id, "timeline"] as const,
  caseAudit: (id: number) => ["cases", id, "audit"] as const,
  sar: (id: number) => ["cases", id, "sar"] as const,
  investigation: (id: number) => ["investigations", id] as const,
  investigationsForClient: (id: number) => ["investigations", "client", id] as const,
}

function retry(failureCount: number, error: unknown) {
  if (error instanceof ApiError && (error.isNotFound || error.status === 409)) return false
  return failureCount < 2
}

/* -------------------------------------------------------------- reference */

export const useHealth = () => useQuery({ queryKey: keys.health, queryFn: fetchHealth, retry, ...OPERATIONAL })
export const useProviders = () => useQuery({ queryKey: keys.providers, queryFn: api.providers, retry, ...REFERENCE })
export const useDatasetStatus = () =>
  useQuery({ queryKey: keys.datasets, queryFn: api.datasetStatus, retry, ...OPERATIONAL })
export const useSources = () => useQuery({ queryKey: keys.sources, queryFn: api.sources, retry, ...REFERENCE })
export const useRiskFactors = () =>
  useQuery({ queryKey: keys.riskFactors, queryFn: api.riskFactors, retry, ...REFERENCE })
export const useAgentStatus = () =>
  useQuery({ queryKey: keys.agentStatus, queryFn: api.agentStatus, retry, ...OPERATIONAL })

/* ------------------------------------------------------------ operational */

export const useCaseMetrics = () =>
  useQuery({ queryKey: keys.caseMetrics, queryFn: api.caseMetrics, retry, ...OPERATIONAL })

export const useCustomers = (params: {
  limit?: number
  offset?: number
  sanctions_flag?: boolean
  pep_flag?: boolean
  sector_risk?: string
  mapped_only?: boolean
}) => useQuery({ queryKey: keys.customers(params), queryFn: () => api.customers(params), retry, ...OPERATIONAL })

export const useCustomer360 = (clientId: number | undefined) =>
  useQuery({
    queryKey: keys.customer360(clientId ?? 0),
    queryFn: () => api.customer360(clientId as number),
    enabled: clientId !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useCurrentRisk = (clientId: number | undefined) =>
  useQuery({
    queryKey: keys.currentRisk(clientId ?? 0),
    queryFn: () => api.currentRisk(clientId as number),
    enabled: clientId !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useRiskHistory = (clientId: number | undefined) =>
  useQuery({
    queryKey: keys.riskHistory(clientId ?? 0),
    queryFn: () => api.riskHistory(clientId as number),
    enabled: clientId !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useRiskEvents = (clientId: number | undefined) =>
  useQuery({
    queryKey: keys.riskEvents(clientId ?? 0),
    queryFn: () => api.riskEvents(clientId as number),
    enabled: clientId !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useAlerts = (params: {
  client_id?: number
  status?: string
  severity?: string
  limit?: number
  offset?: number
}) => useQuery({ queryKey: keys.alerts(params), queryFn: () => api.alerts(params), retry, ...OPERATIONAL })

export const useCases = (params: { status?: CaseStatus; limit?: number; offset?: number }) =>
  useQuery({ queryKey: keys.cases(params), queryFn: () => api.cases(params), retry, ...OPERATIONAL })

export const useCase = (caseId: number | undefined) =>
  useQuery({
    queryKey: keys.case(caseId ?? 0),
    queryFn: () => api.case(caseId as number),
    enabled: caseId !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useCaseTimeline = (caseId: number | undefined) =>
  useQuery({
    queryKey: keys.caseTimeline(caseId ?? 0),
    queryFn: () => api.caseTimeline(caseId as number),
    enabled: caseId !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useCaseAudit = (caseId: number | undefined) =>
  useQuery({
    queryKey: keys.caseAudit(caseId ?? 0),
    queryFn: () => api.caseAudit(caseId as number),
    enabled: caseId !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useSar = (caseId: number | undefined) =>
  useQuery({
    queryKey: keys.sar(caseId ?? 0),
    queryFn: () => api.sar(caseId as number),
    enabled: caseId !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useInvestigation = (id: number | undefined) =>
  useQuery({
    queryKey: keys.investigation(id ?? 0),
    queryFn: () => api.investigation(id as number),
    enabled: id !== undefined,
    retry,
    ...OPERATIONAL,
  })

export const useInvestigationsForClient = (externalClientId: number | undefined) =>
  useQuery({
    queryKey: keys.investigationsForClient(externalClientId ?? 0),
    queryFn: () => api.investigationsForClient(externalClientId as number),
    enabled: externalClientId !== undefined,
    retry,
    ...OPERATIONAL,
  })

/* -------------------------------------------------------------- mutations */

/** Every mutation invalidates the case aggregate AND the metrics: a review
 *  changes both the case and the dashboard counts, and leaving the tile stale
 *  would show a reviewer a number they just disproved. */
export function useSubmitReview(caseId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { reviewer: string; action: ReviewAction; comment?: string; target_id?: number }) =>
      api.submitReview(caseId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.case(caseId) })
      qc.invalidateQueries({ queryKey: keys.caseTimeline(caseId) })
      qc.invalidateQueries({ queryKey: keys.caseAudit(caseId) })
      qc.invalidateQueries({ queryKey: keys.sar(caseId) })
      qc.invalidateQueries({ queryKey: keys.caseMetrics })
      qc.invalidateQueries({ queryKey: ["cases"] })
    },
  })
}

export function useGenerateSar(caseId: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { requested_by: string }) => api.generateSar(caseId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: keys.sar(caseId) })
      qc.invalidateQueries({ queryKey: keys.case(caseId) })
      qc.invalidateQueries({ queryKey: keys.caseTimeline(caseId) })
      qc.invalidateQueries({ queryKey: keys.caseMetrics })
    },
  })
}

export function useRunInvestigation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ externalClientId, reason }: { externalClientId: number; reason?: string }) =>
      api.runInvestigation(externalClientId, { trigger_reason: reason }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: keys.investigationsForClient(vars.externalClientId) })
      qc.invalidateQueries({ queryKey: keys.caseMetrics })
    },
  })
}

export function useOpenCase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { external_client_id: number; reason?: string }) => api.openCase(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cases"] })
      qc.invalidateQueries({ queryKey: keys.caseMetrics })
    },
  })
}

/**
 * Run one deterministic monitoring cycle for a client.
 *
 * This is what turns an empty case workspace into a populated one. A case
 * opened on a client that has never been monitored is legitimately empty --
 * no score, no events, no evidence, no timeline -- because NOTHING HAS RUN.
 * That is the backend being truthful, not broken, and the UI must not invent
 * a score to fill the space. What it CAN do is offer the action that produces
 * the missing data honestly.
 *
 * The cycle is deterministic (ADR-019/022): the score comes from the config-
 * driven risk engine, never from a model. Invalidating `["cases"]` and the
 * client's risk/timeline keys is what makes the workspace refresh in place.
 */
export function useMonitorClient(caseId?: number) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (externalClientId: number) => api.monitorClient(externalClientId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["cases"] })
      qc.invalidateQueries({ queryKey: ["risk"] })
      qc.invalidateQueries({ queryKey: ["alerts"] })
      qc.invalidateQueries({ queryKey: keys.caseMetrics })
      if (caseId !== undefined) {
        qc.invalidateQueries({ queryKey: keys.case(caseId) })
        qc.invalidateQueries({ queryKey: keys.caseTimeline(caseId) })
        qc.invalidateQueries({ queryKey: keys.caseAudit(caseId) })
      }
    },
  })
}
