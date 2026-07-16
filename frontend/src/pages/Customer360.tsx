/**
 * Page 3 -- Customer 360.
 *
 * Composes six real endpoints for one client: /customers/{id}/360, /risk/{id},
 * /risk/history/{id}, /events/{id}, /investigations/client/{externalId}, and
 * /alerts?client_id=.
 *
 * Two honesty rules run through the page:
 *  - A never-scored client shows "Not assessed", never 0/LOW. The backend
 *    returns never_monitored=true with a null score precisely so the UI cannot
 *    claim "we assessed them and they're fine".
 *  - Ownership renders the backend's own `ownership_note`. Phase 0 established
 *    the UBO fixtures share no identifier with the client master, so there is
 *    genuinely nothing to show -- and a fabricated graph here would be the worst
 *    possible guess.
 */

import { useMemo } from "react"
import { Link, useParams } from "react-router-dom"
import { ArrowLeft, Bot, FileText, Network, Radar, ShieldAlert } from "lucide-react"
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingBlock,
} from "@/components/ui"
import { ActorChip, EvidenceCard, ProviderBadge, RiskBadge, TierBadge } from "@/components/domain"
import {
  useAlerts,
  useCurrentRisk,
  useCustomer360,
  useInvestigationsForClient,
  useOpenCase,
  useRiskEvents,
  useRiskHistory,
} from "@/hooks/queries"
import { fmtDateTime, fmtOrDash, humanize } from "@/lib/utils"
import type { FactorContribution } from "@/api/types"
import { Page } from "./Dashboard"

export default function Customer360Page() {
  const { clientId } = useParams()
  const id = Number(clientId)

  const c360 = useCustomer360(id)
  const risk = useCurrentRisk(id)
  const history = useRiskHistory(id)
  const events = useRiskEvents(id)
  const externalId = c360.data?.client.external_client_id
  const investigations = useInvestigationsForClient(externalId)
  const alerts = useAlerts({ client_id: id, limit: 20 })
  const openCase = useOpenCase()

  const contributions = useMemo<FactorContribution[]>(() => {
    const raw = risk.data?.current?.factor_contributions
    if (!raw) return []
    try {
      const parsed = JSON.parse(raw)
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  }, [risk.data])

  const historySeries = useMemo(
    () =>
      [...(history.data?.snapshots ?? [])]
        .reverse()
        .map((s) => ({ date: new Date(s.computed_at).toLocaleDateString(), score: s.current_score })),
    [history.data],
  )

  if (c360.isLoading) return <LoadingBlock label="Loading customer" rows={8} />
  if (c360.error)
    return (
      <Page title="Customer">
        <ErrorState title="Could not load customer" detail={(c360.error as Error).message} />
      </Page>
    )

  const client = c360.data!.client
  const txn = c360.data!.shallow_transaction_summary

  return (
    <Page
      title={client.client_name}
      subtitle={`Client ${client.external_client_id} - ${client.client_type} - ${client.country}`}
      actions={
        <div className="flex gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link to="/customers">
              <ArrowLeft className="h-3.5 w-3.5" /> Customers
            </Link>
          </Button>
          <Button
            size="sm"
            disabled={openCase.isPending}
            onClick={() => openCase.mutate({ external_client_id: client.external_client_id, reason: "Opened from Customer 360" })}
          >
            {openCase.isPending ? "Opening..." : "Open case"}
          </Button>
        </div>
      }
    >
      {openCase.isError ? <ErrorState title="Could not open case" detail={(openCase.error as Error).message} /> : null}
      {openCase.isSuccess ? (
        <div className="rounded border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-800">
          Case {openCase.data.case.case_ref} is open.{" "}
          <Link className="underline" to={`/cases/${openCase.data.case.id}`}>
            Go to case
          </Link>
        </div>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-3">
        {/* ------------------------------------------------ profile */}
        <Card>
          <CardHeader>
            <CardTitle>Customer profile</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5 text-xs">
            <Row label="Sector" value={client.sector} />
            <Row label="Sector risk" value={<Badge variant={client.sector_risk === "High" ? "destructive" : "muted"}>{client.sector_risk}</Badge>} />
            <Row label="Ownership opacity" value={client.ownership_opacity_score.toFixed(2)} />
            <Row label="Accounts" value={c360.data!.accounts.length} />
            <Row label="Provenance" value={<TierBadge tier={client.source_tier} />} />
            <div className="mt-2 border-t pt-2">
              <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                Upstream labels -- carried on the client master, NOT verified by this platform
              </p>
              <div className="flex flex-wrap gap-1">
                {client.sanctions_flag ? <Badge variant="destructive">Sanctions</Badge> : null}
                {client.pep_flag ? <Badge variant="warning">PEP</Badge> : null}
                {client.fatf_country_flag ? <Badge variant="outline">FATF country</Badge> : null}
                {client.ofac_country_flag ? <Badge variant="outline">OFAC country</Badge> : null}
                {client.sectoral_sanctions_flag ? <Badge variant="outline">Sectoral</Badge> : null}
                {!client.sanctions_flag && !client.pep_flag && !client.fatf_country_flag ? (
                  <span className="text-[10px] text-muted-foreground">None</span>
                ) : null}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* ------------------------------------------------ risk */}
        <Card>
          <CardHeader>
            <CardTitle>Risk score</CardTitle>
          </CardHeader>
          <CardContent>
            {risk.isLoading ? (
              <LoadingBlock label="Loading risk" rows={2} />
            ) : risk.data?.never_monitored || !risk.data?.current ? (
              <EmptyState
                icon={Radar}
                title="Not assessed"
                description="This client has never been scored. That is different from being low risk -- nobody has looked yet."
              />
            ) : (
              <div className="space-y-2">
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-semibold tabular-nums">{risk.data.current.current_score}</span>
                  <span className="text-xs text-muted-foreground">/ 100</span>
                  <RiskBadge band={risk.data.current.risk_band} />
                </div>
                <p className="text-xs text-muted-foreground">{risk.data.current.trigger_reason}</p>
                <p className="text-[10px] text-muted-foreground">
                  Computed {fmtDateTime(risk.data.current.computed_at)} - logic{" "}
                  {risk.data.current.scoring_logic_version}
                </p>
                <p className="rounded bg-muted/60 p-1.5 text-[10px] text-muted-foreground">
                  Computed by deterministic application logic. No language model contributed to this number.
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ------------------------------------------------ factors */}
        <Card>
          <CardHeader>
            <CardTitle>Risk factors</CardTitle>
          </CardHeader>
          <CardContent>
            {contributions.length === 0 ? (
              <EmptyState title="No factor contributions" description="Nothing has contributed to a score yet." />
            ) : (
              <ul className="space-y-2">
                {contributions.map((f) => (
                  <li key={f.factor_id} className="text-xs">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{f.factor_name}</span>
                      <span className="font-mono tabular-nums text-primary">+{f.contribution}</span>
                    </div>
                    <p className="text-[10px] text-muted-foreground">{f.reason}</p>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ------------------------------------------------ history chart */}
      <Card>
        <CardHeader>
          <CardTitle>Risk history</CardTitle>
        </CardHeader>
        <CardContent className="h-56">
          {history.isLoading ? (
            <LoadingBlock label="Loading risk history" />
          ) : historySeries.length === 0 ? (
            <EmptyState icon={Radar} title="No risk history" description="Run a monitoring cycle to build history." />
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={historySeries} margin={{ top: 8, right: 12, bottom: 0, left: -20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(214 32% 91%)" />
                <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} />
                <Tooltip />
                <Line type="stepAfter" dataKey="score" stroke="hsl(217 91% 35%)" strokeWidth={2} dot={{ r: 2 }} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* ------------------------------------------------ evidence */}
        <Card>
          <CardHeader>
            <CardTitle>Evidence ({c360.data!.evidence.length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {c360.data!.evidence.length === 0 ? (
              <EmptyState
                icon={FileText}
                title="No evidence on file"
                description="An empty evidence base, not a finding of no risk."
              />
            ) : (
              c360.data!.evidence.slice(0, 8).map((e) => <EvidenceCard key={e.id} evidence={e} />)
            )}
          </CardContent>
        </Card>

        <div className="space-y-3">
          {/* --------------------------------------- entity matches */}
          <Card>
            <CardHeader>
              <CardTitle>Entity matches</CardTitle>
            </CardHeader>
            <CardContent>
              {c360.data!.sanctions_candidates.length === 0 ? (
                <EmptyState
                  title="No sanctions candidates"
                  description="Phase 0 measured 0/2000 client names matching the authoritative lists -- this is the expected, honest result."
                />
              ) : (
                <p className="text-xs">{c360.data!.sanctions_candidates.length} candidate(s)</p>
              )}
            </CardContent>
          </Card>

          {/* --------------------------------------- providers */}
          <Card>
            <CardHeader>
              <CardTitle>Provider results</CardTitle>
            </CardHeader>
            <CardContent>
              {c360.data!.provider_availability.length === 0 ? (
                <EmptyState
                  title="No live provider queries"
                  description="Customer 360 does not fire live lookups by default -- they are opt-in (ADR-009)."
                />
              ) : (
                <ul className="space-y-1.5">
                  {c360.data!.provider_availability.map((p) => (
                    <li key={p.provider_name} className="flex items-center justify-between gap-2 text-xs">
                      <span className="truncate">{p.provider_name}</span>
                      <ProviderBadge status={p.status} />
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          {/* --------------------------------------- ownership */}
          <Card>
            <CardHeader>
              <CardTitle>Ownership / UBO</CardTitle>
            </CardHeader>
            <CardContent>
              <EmptyState icon={Network} title="No ownership graph linked" description={c360.data!.ownership_note} />
            </CardContent>
          </Card>

          {/* --------------------------------------- transactions */}
          <Card>
            <CardHeader>
              <CardTitle>Transactions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 text-xs">
              <Row label="Count" value={fmtOrDash(txn.transaction_count)} />
              <Row label="Total amount" value={fmtOrDash(txn.total_amount)} />
              <Row label="Flagged" value={fmtOrDash(txn.flagged_count)} />
              {/* null and 0 are rendered differently on purpose. */}
              <Row
                label="Laundering-labelled"
                value={
                  txn.laundering_labelled_count === null ? (
                    <span className="text-muted-foreground" title="This source carries no laundering label. An absence of data, not a finding of none.">
                      Not available
                    </span>
                  ) : (
                    txn.laundering_labelled_count
                  )
                }
              />
            </CardContent>
          </Card>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* --------------------------------------- alerts */}
        <Card>
          <CardHeader>
            <CardTitle>Alerts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {alerts.isLoading ? (
              <LoadingBlock label="Loading alerts" />
            ) : alerts.data?.alerts.length ? (
              alerts.data.alerts.map((a) => (
                <div key={a.id} className="flex items-center gap-2 rounded border p-2 text-xs">
                  <RiskBadge band={a.severity} />
                  <span className="truncate">{a.reason}</span>
                  <span className="ml-auto shrink-0 text-[10px] text-muted-foreground">{fmtDateTime(a.opened_at)}</span>
                </div>
              ))
            ) : (
              <EmptyState icon={ShieldAlert} title="No alerts" />
            )}
          </CardContent>
        </Card>

        {/* --------------------------------------- investigations */}
        <Card>
          <CardHeader>
            <CardTitle>Investigations</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {investigations.isLoading ? (
              <LoadingBlock label="Loading investigations" />
            ) : investigations.data?.investigations.length ? (
              investigations.data.investigations.map((i) => (
                <Link
                  key={i.id}
                  to={`/investigations/${i.id}`}
                  className="block rounded border p-2 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="flex items-center gap-2">
                    <ActorChip actor="AGENT" id="investigation agent" />
                    <Badge variant={i.status === "FAILED" ? "destructive" : "muted"}>{humanize(i.status)}</Badge>
                    <span className="ml-auto text-[10px] text-muted-foreground">{fmtDateTime(i.opened_at)}</span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-muted-foreground">{i.summary ?? i.error_message ?? "--"}</p>
                </Link>
              ))
            ) : (
              <EmptyState
                icon={Bot}
                title="No investigations"
                description="Open a case and run an investigation from the case workspace."
              />
            )}
          </CardContent>
        </Card>
      </div>

      {/* --------------------------------------- timeline preview */}
      <Card>
        <CardHeader>
          <CardTitle>Risk events (timeline preview)</CardTitle>
        </CardHeader>
        <CardContent>
          {events.isLoading ? (
            <LoadingBlock label="Loading events" />
          ) : events.data?.events.length ? (
            <ul className="space-y-1.5">
              {events.data.events.slice(0, 8).map((e) => (
                <li key={e.id} className="flex items-start gap-2 text-xs">
                  <RiskBadge band={e.severity} />
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{humanize(e.event_type)}</span>
                    <span className="ml-2 text-muted-foreground">{e.summary}</span>
                  </span>
                  <span className="shrink-0 text-[10px] text-muted-foreground">{fmtDateTime(e.detected_at)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={Radar} title="No risk events" />
          )}
        </CardContent>
      </Card>
    </Page>
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
