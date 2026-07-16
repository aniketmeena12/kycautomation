/**
 * Page 9 -- System Health.
 *
 * SCOPE NOTE: the brief asks for "API latency" and "monitoring cycles". The
 * backend exposes neither as an endpoint. Rather than invent figures, this page:
 *  - measures API latency IN THE BROWSER and labels it as a client-side round
 *    trip, not a server-side APM metric;
 *  - reports LLM latency from /cases/metrics, which IS a real server figure;
 *  - reports monitoring coverage from /datasets/status and the health checks.
 */

import { useEffect, useState } from "react"
import { Activity, Database, Server } from "lucide-react"
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingBlock,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui"
import { ProviderConfigBadge } from "@/components/domain"
import { useAgentStatus, useCaseMetrics, useDatasetStatus, useHealth, useProviders } from "@/hooks/queries"
import { fmtDateTime, fmtOrDash, humanize } from "@/lib/utils"
import { Page } from "./Dashboard"

export default function SystemHealthPage() {
  const health = useHealth()
  const providers = useProviders()
  const datasets = useDatasetStatus()
  const metrics = useCaseMetrics()
  const agent = useAgentStatus()
  const [apiLatency, setApiLatency] = useState<number | null>(null)

  // A real measurement of THIS client's round trip, labelled as such. Faking a
  // server-side APM number would be exactly the invented metric the brief bans.
  useEffect(() => {
    let cancelled = false
    const started = performance.now()
    fetch("/health/live")
      .then(() => {
        if (!cancelled) setApiLatency(Math.round(performance.now() - started))
      })
      .catch(() => {
        if (!cancelled) setApiLatency(null)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <Page title="System Health" subtitle="Live status read from the backend.">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Card>
          <CardContent className="p-4">
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Server className="h-3.5 w-3.5" /> API
            </p>
            <p className="mt-2 text-2xl font-semibold">
              {health.isLoading ? "..." : health.error ? "Down" : humanize(health.data?.status)}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Activity className="h-3.5 w-3.5" /> API round trip
            </p>
            <p className="mt-2 text-2xl font-semibold tabular-nums">{fmtOrDash(apiLatency, " ms")}</p>
            <p className="mt-1 text-[10px] text-muted-foreground">Measured in this browser, not server-side APM.</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Activity className="h-3.5 w-3.5" /> Mean LLM latency
            </p>
            <p className="mt-2 text-2xl font-semibold tabular-nums">
              {fmtOrDash(
                metrics.data?.average_investigation_latency_ms
                  ? Math.round(metrics.data.average_investigation_latency_ms)
                  : null,
                " ms",
              )}
            </p>
            <p className="mt-1 text-[10px] text-muted-foreground">Over investigations that produced a report.</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Database className="h-3.5 w-3.5" /> Database
            </p>
            <p className="mt-2 text-2xl font-semibold">
              {health.data?.checks.find((c) => c.name === "database")?.status === "ok" ? "OK" : "--"}
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Health checks</CardTitle>
          </CardHeader>
          <CardContent>
            {health.isLoading ? (
              <LoadingBlock label="Loading health" />
            ) : health.error ? (
              <ErrorState title="Backend unreachable" detail={(health.error as Error).message} />
            ) : (
              <ul className="space-y-1.5">
                {health.data?.checks.map((c) => (
                  <li key={c.name} className="flex items-start justify-between gap-2 text-xs">
                    <span className="min-w-0">
                      <span className="font-medium">{humanize(c.name)}</span>
                      {c.detail ? <p className="text-[10px] text-muted-foreground">{c.detail}</p> : null}
                    </span>
                    <Badge variant={c.status === "ok" ? "success" : "destructive"}>{c.status}</Badge>
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
              <LoadingBlock label="Loading agent" />
            ) : agent.error ? (
              <ErrorState detail={(agent.error as Error).message} />
            ) : (
              <>
                <div className="flex justify-between gap-2">
                  <span className="text-muted-foreground">Provider</span>
                  <span className="font-mono">{agent.data?.provider}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted-foreground">Model</span>
                  <span className="font-mono text-[10px]">{agent.data?.model}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted-foreground">Prompt version</span>
                  <span className="font-mono">{agent.data?.prompt_version}</span>
                </div>
                <div className="flex justify-between gap-2">
                  <span className="text-muted-foreground">Configured</span>
                  {agent.data?.configured ? <Badge variant="success">Ready</Badge> : <Badge variant="muted">No</Badge>}
                </div>
                <p className="pt-1 text-[10px] text-muted-foreground">{agent.data?.note}</p>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Provider availability</CardTitle>
        </CardHeader>
        <CardContent>
          {providers.isLoading ? (
            <LoadingBlock label="Loading providers" />
          ) : providers.error ? (
            <ErrorState detail={(providers.error as Error).message} />
          ) : providers.data?.providers?.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Provider</TableHead>
                  <TableHead>Kind</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {providers.data.providers.map((p) => (
                  <TableRow key={p.provider_name}>
                    <TableCell className="font-medium">{p.provider_name}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{humanize(p.provider_kind)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{humanize(p.category)}</TableCell>
                    <TableCell>
                      <ProviderConfigBadge configured={p.configured} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState title="No providers registered" />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Dataset status</CardTitle>
          <p className="text-[10px] text-muted-foreground">
            Monitoring coverage: which sources are ingested and available to the engine.
          </p>
        </CardHeader>
        <CardContent>
          {datasets.isLoading ? (
            <LoadingBlock label="Loading datasets" />
          ) : datasets.error ? (
            <ErrorState detail={(datasets.error as Error).message} />
          ) : datasets.data?.statuses?.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Source</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Records</TableHead>
                  <TableHead>Last ingested</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {datasets.data.statuses.map((d) => (
                  <TableRow key={d.source_key}>
                    <TableCell className="font-mono text-xs">{d.source_key}</TableCell>
                    <TableCell>
                      <Badge
                        variant={
                          d.status === "LOADED" ? "success" : d.status === "NOT_INGESTED" ? "muted" : "warning"
                        }
                      >
                        {humanize(d.status)}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs tabular-nums">{fmtOrDash(d.records_loaded)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{fmtDateTime(d.last_ingested_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState title="No dataset status" description="Run ingestion: POST /api/v1/ingestion/load" />
          )}
        </CardContent>
      </Card>
    </Page>
  )
}
