/**
 * Page 1 -- Executive Dashboard.
 *
 * EVERY NUMBER ON THIS PAGE COMES FROM A REAL ENDPOINT. Where the brief asks
 * for a tile the backend has no aggregate for, the tile says so rather than
 * inventing one -- see the two honest gaps below.
 *
 * WHAT IS DERIVED, AND HOW (all real):
 *  - Alert severity mix: 4 calls to /alerts?severity=X&limit=1 reading `total`
 *    -- /alerts DOES wrap its rows, so each probe is one tiny request.
 *  - Portfolio sector-risk mix and flagged counts: /customers returns a BARE
 *    ARRAY with no total and a 500 cap, so each count pages until a short page
 *    (4 requests for 2,000 clients, cached 5 min). Assuming a `{clients,total}`
 *    wrapper here is what made every tile render "--" in the first version.
 *
 * WHAT IS NOT AVAILABLE, AND IS LABELLED AS SUCH:
 *  - Computed risk-BAND distribution across customers. There is no aggregate
 *    endpoint, and deriving it would mean one /risk/{id} call per client --
 *    2,000 requests to render one chart. The page shows the sector-risk mix
 *    (which IS a real aggregate) and states plainly that it is the client
 *    master's own risk label, not the engine's computed band.
 *  - A global activity feed. Audit is exposed per-case only
 *    (/cases/{id}/audit); there is no /audit endpoint. "Recent activity" is
 *    therefore built from the newest cases/alerts/investigations we can
 *    legitimately list.
 */

import { useQueries } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import {
  Bot,
  ClipboardList,
  FileText,
  Radar,
  ShieldAlert,
  TriangleAlert,
  UserCheck,
  Users,
} from "lucide-react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { api } from "@/api/client"
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingBlock,
} from "@/components/ui"
import { AlertCard, ProviderConfigBadge, RiskBadge, StatCard, StatusChip } from "@/components/domain"
import {
  useAgentStatus,
  useAlerts,
  useCaseMetrics,
  useCases,
  useDatasetStatus,
  useHealth,
  useProviders,
} from "@/hooks/queries"
import { fmtDateTime, fmtOrDash } from "@/lib/utils"

const CHART_COLORS = ["hsl(142 45% 36%)", "hsl(38 78% 42%)", "hsl(24 82% 46%)", "hsl(0 72% 45%)"]

/**
 * Portfolio aggregates.
 *
 * `/alerts` wraps its rows in `{alerts, total}`, so a `limit=1` probe reading
 * `total` is one tiny request. `/customers` does NOT -- it returns a bare array
 * with no total and a 500 cap -- so a real count means paging (see
 * api.countCustomers). The shapes are asymmetric; assuming otherwise is exactly
 * what made every tile render "--" in the first version of this page.
 *
 * Cached for 5 minutes: the portfolio does not change between page views, and
 * these are the heaviest calls the dashboard makes.
 */
function useAggregates() {
  const customerCount = (filter: Parameters<typeof api.countCustomers>[0], key: string) => ({
    queryKey: ["agg", "customers", key],
    queryFn: () => api.countCustomers(filter),
    staleTime: 300_000,
  })
  const alertCount = (severity: string) => ({
    queryKey: ["agg", "alerts", severity],
    queryFn: () => api.alerts({ limit: 1, severity }),
    staleTime: 300_000,
  })
  return useQueries({
    queries: [
      customerCount({}, "all"),
      customerCount({ sector_risk: "High" }, "High"),
      customerCount({ sector_risk: "Medium" }, "Medium"),
      customerCount({ sector_risk: "Low" }, "Low"),
      customerCount({ sanctions_flag: true }, "sanctions"),
      customerCount({ pep_flag: true }, "pep"),
      alertCount("CRITICAL"),
      alertCount("HIGH"),
      alertCount("MEDIUM"),
      alertCount("LOW"),
    ],
  })
}

export default function Dashboard() {
  const aggregates = useAggregates()
  const metrics = useCaseMetrics()
  const providers = useProviders()
  const datasets = useDatasetStatus()
  const agent = useAgentStatus()
  const health = useHealth()
  const recentAlerts = useAlerts({ limit: 5 })
  const recentCases = useCases({ limit: 5 })

  const [all, high, medium, low, sanctioned, pep, critA, highA, medA, lowA] = aggregates
  const loading = aggregates.some((q) => q.isLoading) || metrics.isLoading
  const aggregateError = aggregates.find((q) => q.error)?.error

  if (aggregateError) {
    return (
      <Page title="Executive Dashboard">
        <ErrorState title="Could not load portfolio data" detail={(aggregateError as Error).message} />
      </Page>
    )
  }

  const sectorMix = [
    { name: "Low", value: low.data?.count ?? 0 },
    { name: "Medium", value: medium.data?.count ?? 0 },
    { name: "High", value: high.data?.count ?? 0 },
  ]
  const alertMix = [
    { name: "LOW", value: lowA.data?.total ?? 0 },
    { name: "MEDIUM", value: medA.data?.total ?? 0 },
    { name: "HIGH", value: highA.data?.total ?? 0 },
    { name: "CRITICAL", value: critA.data?.total ?? 0 },
  ]
  const hasAlerts = alertMix.some((a) => a.value > 0)

  const caseMix = metrics.data
    ? [
        { name: "Open", value: metrics.data.open_cases },
        { name: "Under review", value: metrics.data.under_review_cases },
        { name: "Escalated", value: metrics.data.escalated_cases },
        { name: "SAR review", value: metrics.data.sar_review_cases },
        { name: "Closed", value: metrics.data.closed_cases },
      ]
    : []

  return (
    <Page title="Executive Dashboard" subtitle="Live portfolio posture. Every figure is read from the backend.">
      {loading ? (
        <LoadingBlock label="Loading dashboard metrics" rows={4} />
      ) : (
        <>
          {/* ------------------------------------------------ stat tiles */}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
            <StatCard label="Total customers" value={fmtOrDash(all.data?.count)} icon={Users} to="/customers" />
            <StatCard
              label="High-risk sector"
              value={fmtOrDash(high.data?.count)}
              hint="Client-master sector label"
              icon={TriangleAlert}
              tone="warning"
              to="/customers?sector_risk=High"
            />
            <StatCard
              label="Critical alerts"
              value={fmtOrDash(critA.data?.total)}
              icon={ShieldAlert}
              tone={critA.data?.total ? "danger" : "default"}
            />
            <StatCard
              label="Open cases"
              value={fmtOrDash(metrics.data?.open_cases)}
              icon={ClipboardList}
              to="/cases?status=OPEN"
            />
            <StatCard
              label="Pending reviews"
              value={fmtOrDash((metrics.data?.under_review_cases ?? 0) + (metrics.data?.escalated_cases ?? 0))}
              hint="Under review + escalated"
              icon={UserCheck}
              tone="warning"
              to="/cases?status=UNDER_REVIEW"
            />
            <StatCard
              label="SAR drafts pending"
              value={fmtOrDash(metrics.data?.sar_pending)}
              icon={FileText}
              to="/cases?status=SAR_REVIEW"
            />
          </div>

          {/* --------------------------------------- status strip */}
          <div className="grid gap-3 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Monitoring status</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1.5 text-xs">
                <Row label="Database" value={<HealthChip check={health.data?.checks.find((c) => c.name === "database")?.status} />} />
                <Row
                  label="Dataset registry"
                  value={<HealthChip check={health.data?.checks.find((c) => c.name === "dataset_registry")?.status} />}
                />
                <Row label="Sanctioned (upstream flag)" value={fmtOrDash(sanctioned.data?.count)} />
                <Row label="PEP (upstream flag)" value={fmtOrDash(pep.data?.count)} />
                <Row label="Investigations run" value={fmtOrDash(metrics.data?.investigations_total)} />
                <Row
                  label="Investigations failed"
                  value={fmtOrDash(metrics.data?.investigations_failed)}
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Provider status</CardTitle>
              </CardHeader>
              <CardContent>
                {providers.isLoading ? (
                  <LoadingBlock label="Loading providers" rows={3} />
                ) : providers.error ? (
                  <ErrorState detail={(providers.error as Error).message} />
                ) : (
                  <ul className="space-y-1.5">
                    {providers.data?.providers?.slice(0, 6).map((p) => (
                      <li key={p.provider_name} className="flex items-center justify-between gap-2 text-xs">
                        <span className="truncate">{p.provider_name}</span>
                        <ProviderConfigBadge configured={p.configured} />
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Investigation agent</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1.5 text-xs">
                {agent.isLoading ? (
                  <LoadingBlock label="Loading agent status" rows={2} />
                ) : agent.error ? (
                  <ErrorState detail={(agent.error as Error).message} />
                ) : (
                  <>
                    <Row label="Provider" value={<span className="font-mono">{agent.data?.provider}</span>} />
                    <Row label="Model" value={<span className="font-mono text-[10px]">{agent.data?.model}</span>} />
                    <Row label="Prompt version" value={<span className="font-mono">{agent.data?.prompt_version}</span>} />
                    <Row
                      label="Configured"
                      value={
                        agent.data?.configured ? (
                          <Badge variant="success">Ready</Badge>
                        ) : (
                          <Badge variant="muted" title={agent.data?.note}>
                            Not configured
                          </Badge>
                        )
                      }
                    />
                    <Row
                      label="Mean latency"
                      value={fmtOrDash(
                        metrics.data?.average_investigation_latency_ms
                          ? Math.round(metrics.data.average_investigation_latency_ms)
                          : null,
                        " ms",
                      )}
                    />
                  </>
                )}
              </CardContent>
            </Card>
          </div>

          {/* ------------------------------------------------- charts */}
          <div className="grid gap-3 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Portfolio risk distribution</CardTitle>
                {/* Labelled honestly. This is the client master's sector-risk
                    label, NOT the engine's computed band -- no aggregate exists
                    for the latter (see module docstring). */}
                <p className="text-[10px] text-muted-foreground">
                  Sector-risk label from the client master. Not the engine's computed band -- no aggregate
                  endpoint exists for that.
                </p>
              </CardHeader>
              <CardContent className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie data={sectorMix} dataKey="value" nameKey="name" innerRadius={45} outerRadius={72}>
                      {sectorMix.map((_, i) => (
                        <Cell key={i} fill={CHART_COLORS[i]} />
                      ))}
                    </Pie>
                    <Tooltip />
                    <Legend iconSize={8} wrapperStyle={{ fontSize: 11 }} />
                  </PieChart>
                </ResponsiveContainer>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Alert severity mix</CardTitle>
                <p className="text-[10px] text-muted-foreground">Counts from /alerts, by severity.</p>
              </CardHeader>
              <CardContent className="h-56">
                {hasAlerts ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={alertMix} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(214 32% 91%)" />
                      <XAxis dataKey="name" tick={{ fontSize: 10 }} />
                      <YAxis allowDecimals={false} tick={{ fontSize: 10 }} />
                      <Tooltip />
                      <Bar dataKey="value" radius={[3, 3, 0, 0]}>
                        {alertMix.map((_, i) => (
                          <Cell key={i} fill={CHART_COLORS[i]} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState
                    icon={ShieldAlert}
                    title="No alerts yet"
                    description="Run a monitoring cycle to generate alerts."
                  />
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Case status</CardTitle>
                <p className="text-[10px] text-muted-foreground">From /cases/metrics.</p>
              </CardHeader>
              <CardContent className="h-56">
                {metrics.data?.total_cases ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={caseMix} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 24 }}>
                      <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(214 32% 91%)" />
                      <XAxis type="number" allowDecimals={false} tick={{ fontSize: 10 }} />
                      <YAxis type="category" dataKey="name" width={72} tick={{ fontSize: 10 }} />
                      <Tooltip />
                      <Bar dataKey="value" fill="hsl(217 91% 35%)" radius={[0, 3, 3, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <EmptyState icon={ClipboardList} title="No cases yet" description="Open a case from a customer." />
                )}
              </CardContent>
            </Card>
          </div>

          {/* --------------------------------------- recent activity */}
          <div className="grid gap-3 lg:grid-cols-2">
            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Recent alerts</CardTitle>
                <Link to="/cases" className="text-xs text-primary hover:underline">
                  View cases
                </Link>
              </CardHeader>
              <CardContent className="space-y-2">
                {recentAlerts.isLoading ? (
                  <LoadingBlock label="Loading alerts" />
                ) : recentAlerts.data?.alerts.length ? (
                  recentAlerts.data.alerts.map((a) => <AlertCard key={a.id} alert={a} />)
                ) : (
                  <EmptyState icon={ShieldAlert} title="No alerts on record" />
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex-row items-center justify-between">
                <CardTitle>Recent cases</CardTitle>
                <Link to="/cases" className="text-xs text-primary hover:underline">
                  All cases
                </Link>
              </CardHeader>
              <CardContent className="space-y-2">
                {recentCases.isLoading ? (
                  <LoadingBlock label="Loading cases" />
                ) : recentCases.data?.cases.length ? (
                  recentCases.data.cases.map((c) => (
                    <Link
                      key={c.id}
                      to={`/cases/${c.id}`}
                      className="flex items-center justify-between gap-2 rounded border p-2 text-xs hover:bg-accent"
                    >
                      <span className="min-w-0">
                        <span className="font-mono">{c.case_ref}</span>
                        <span className="ml-2 truncate">{c.client_name}</span>
                      </span>
                      <span className="flex shrink-0 items-center gap-2">
                        <RiskBadge
                          band={c.current_risk_band}
                          score={c.current_risk_score}
                          neverMonitored={c.current_risk_score === null}
                        />
                        <StatusChip status={c.status} />
                      </span>
                    </Link>
                  ))
                ) : (
                  <EmptyState
                    icon={ClipboardList}
                    title="No cases yet"
                    description="Open a case from the Customers page to start the compliance workflow."
                  />
                )}
              </CardContent>
            </Card>
          </div>

          <p className="text-[10px] text-muted-foreground">
            Dataset sources loaded: {datasets.data?.statuses?.length ?? 0}. Metrics generated{" "}
            {fmtDateTime(metrics.data?.generated_at)}.
          </p>
        </>
      )}
    </Page>
  )
}

/* ------------------------------------------------------------ small bits */

export function Page({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string
  subtitle?: string
  actions?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
          {subtitle ? <p className="text-xs text-muted-foreground">{subtitle}</p> : null}
        </div>
        {actions}
      </header>
      {children}
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  )
}

function HealthChip({ check }: { check?: string }) {
  if (!check) return <Badge variant="muted">Unknown</Badge>
  return check === "ok" ? <Badge variant="success">OK</Badge> : <Badge variant="destructive">{check}</Badge>
}

export { Radar, Bot }
