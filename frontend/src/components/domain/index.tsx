/**
 * Domain components: the reusable vocabulary of this product.
 *
 * These encode compliance meaning, not just styling. Two rules run through all
 * of them and are the reason they exist as shared components rather than
 * inline JSX:
 *
 *  1. TIER IS ALWAYS VISIBLE. ADR-002 says a TIER_2_CURATED_DEMO hit must never
 *     be presentable as an authoritative regulatory finding -- and a UI is a
 *     presentation. EvidenceCard and ProviderBadge therefore render the tier
 *     every time; a caller cannot forget it.
 *
 *  2. SYSTEM / AGENT / HUMAN ARE NEVER INTERCHANGEABLE. An LLM's opinion and a
 *     compliance officer's decision must look different at a glance, so
 *     ActorChip gives each actor a distinct, fixed treatment.
 */

import { Link } from "react-router-dom"
import {
  AlertTriangle,
  Bot,
  Building2,
  CheckCircle2,
  CircleSlash,
  Clock,
  FileText,
  Gavel,
  Radar,
  Search,
  ShieldAlert,
  User,
  XCircle,
  Zap,
} from "lucide-react"
import { Badge, Card, CardContent } from "@/components/ui"
import { cn, fmtDateTime, humanize } from "@/lib/utils"
import type {
  ActorType,
  Alert,
  CaseStatus,
  CaseSummary,
  Evidence,
  ProviderResultStatus,
  RiskBand,
  SourceTier,
  TimelineEntry,
  TimelineEntryType,
} from "@/api/types"

/* --------------------------------------------------------------- StatCard */

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
  to,
}: {
  label: string
  value: React.ReactNode
  hint?: string
  icon?: React.ComponentType<{ className?: string }>
  tone?: "default" | "warning" | "danger" | "success"
  to?: string
}) {
  const tones = {
    default: "text-foreground",
    warning: "text-amber-700",
    danger: "text-destructive",
    success: "text-emerald-700",
  }
  const body = (
    <Card className={cn("h-full", to && "transition-colors hover:border-primary/40")}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="text-xs font-medium text-muted-foreground">{label}</p>
          {Icon ? <Icon className="h-4 w-4 shrink-0 text-muted-foreground" /> : null}
        </div>
        <p className={cn("mt-2 text-2xl font-semibold tabular-nums", tones[tone])}>{value}</p>
        {hint ? <p className="mt-1 text-xs text-muted-foreground">{hint}</p> : null}
      </CardContent>
    </Card>
  )
  return to ? (
    <Link to={to} className="block rounded-lg focus-visible:ring-2 focus-visible:ring-ring">
      {body}
    </Link>
  ) : (
    body
  )
}

/* -------------------------------------------------------------- RiskBadge */

const RISK_STYLES: Record<RiskBand, string> = {
  LOW: "bg-emerald-50 text-emerald-700 border-emerald-200",
  MEDIUM: "bg-amber-50 text-amber-700 border-amber-200",
  HIGH: "bg-orange-50 text-orange-700 border-orange-200",
  CRITICAL: "bg-red-50 text-red-700 border-red-200",
}

export function RiskBadge({
  band,
  score,
  neverMonitored,
}: {
  band?: RiskBand | string | null
  score?: number | null
  /** Rendered distinctly on purpose. The backend returns a null score with
   *  never_monitored=true rather than 0/LOW, because "we assessed them and
   *  they're fine" and "nobody ever looked" are opposite claims. The UI must
   *  not collapse them either. */
  neverMonitored?: boolean
}) {
  if (neverMonitored || !band) {
    return (
      <Badge variant="muted" title="This client has never been scored by the risk engine.">
        Not assessed
      </Badge>
    )
  }
  const key = band as RiskBand
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-semibold",
        RISK_STYLES[key] ?? "bg-muted text-muted-foreground",
      )}
    >
      {key}
      {score !== null && score !== undefined ? (
        <span className="tabular-nums font-mono opacity-80">{score}</span>
      ) : null}
    </span>
  )
}

/* ------------------------------------------------------------- TierBadge */

const TIER_LABEL: Record<SourceTier, { label: string; className: string; title: string }> = {
  TIER_1_AUTHORITATIVE: {
    label: "Tier 1",
    className: "bg-blue-50 text-blue-700 border-blue-200",
    title: "Authoritative reference data (real OFAC / OpenSanctions).",
  },
  TIER_2_CURATED_DEMO: {
    label: "Tier 2 demo",
    className: "bg-violet-50 text-violet-700 border-violet-200",
    title: "Curated DEMONSTRATION fixture. NOT authoritative and never a confirmed regulatory finding.",
  },
  INTERNAL: {
    label: "Internal",
    className: "bg-slate-100 text-slate-700 border-slate-200",
    title: "This platform's own operational records.",
  },
  EXTERNAL_LIVE: {
    label: "External live",
    className: "bg-teal-50 text-teal-700 border-teal-200",
    title: "Retrieved at runtime from an external API.",
  },
}

export function TierBadge({ tier }: { tier?: SourceTier | string | null }) {
  if (!tier) return null
  const meta = TIER_LABEL[tier as SourceTier]
  if (!meta) return <Badge variant="muted">{tier}</Badge>
  return (
    <span
      title={meta.title}
      className={cn("inline-flex items-center rounded border px-1.5 py-0.5 text-[10px] font-medium", meta.className)}
    >
      {meta.label}
    </span>
  )
}

/* -------------------------------------------------------------- StatusChip */

const CASE_STATUS_STYLES: Record<CaseStatus, string> = {
  OPEN: "bg-blue-50 text-blue-700 border-blue-200",
  UNDER_REVIEW: "bg-amber-50 text-amber-700 border-amber-200",
  ESCALATED: "bg-orange-50 text-orange-700 border-orange-200",
  SAR_REVIEW: "bg-violet-50 text-violet-700 border-violet-200",
  CLOSED: "bg-slate-100 text-slate-600 border-slate-200",
}

export function StatusChip({ status }: { status: CaseStatus | string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium",
        CASE_STATUS_STYLES[status as CaseStatus] ?? "bg-muted text-muted-foreground border-border",
      )}
    >
      {humanize(status)}
    </span>
  )
}

/* ----------------------------------------------------------- ProviderBadge */

const PROVIDER_STATUS: Record<ProviderResultStatus, { variant: Parameters<typeof Badge>[0]["variant"]; title: string }> =
  {
    SUCCESS: { variant: "success", title: "Provider responded." },
    NO_RESULTS: { variant: "muted", title: "Provider responded and found nothing. This is a result, not a failure." },
    NOT_CONFIGURED: {
      variant: "muted",
      // The backend is emphatic that NOT_CONFIGURED is not degraded coverage --
      // it's a provider that was never expected to answer (Phase 4 SS6).
      title: "No credentials configured. This provider was never expected to answer -- not a failure.",
    },
    RATE_LIMITED: { variant: "warning", title: "Rate limited." },
    TIMEOUT: { variant: "warning", title: "Timed out. Coverage for this check is INCOMPLETE." },
    ERROR: { variant: "destructive", title: "Provider error. Coverage for this check is INCOMPLETE." },
  }

export function ProviderBadge({ status }: { status: ProviderResultStatus | string }) {
  const meta = PROVIDER_STATUS[status as ProviderResultStatus]
  return (
    <Badge variant={meta?.variant ?? "muted"} title={meta?.title}>
      {humanize(status)}
    </Badge>
  )
}

/* --------------------------------------------------- ProviderConfigBadge */

/**
 * Registration state -- NOT execution state. These are different facts and must
 * never share a badge.
 *
 * `GET /providers` returns exactly {provider_name, provider_kind, category,
 * configured}. There is no status field, because nothing has been RUN: the
 * catalogue lists what is registered, not what answered. An earlier version of
 * this file rendered `configured ? "SUCCESS" : "NOT_CONFIGURED"`, which told a
 * reviewer that a sanctions provider had responded successfully when it had
 * done nothing at all. That is the single most dangerous lie this screen could
 * tell -- "sanctions screening: Success" is precisely the claim a compliance
 * officer would rely on -- and the backend never made it.
 *
 * A provider's real ProviderResultStatus only exists on a ProviderResult row,
 * produced by an actual execution against a named entity. Use ProviderBadge
 * there; use this badge for the catalogue.
 */
export function ProviderConfigBadge({ configured }: { configured: boolean }) {
  return configured ? (
    <Badge variant="outline" title="Registered and ready to be called. It has NOT been run -- this is not a result.">
      Ready
    </Badge>
  ) : (
    <Badge variant="muted" title="No credentials configured. This provider was never expected to answer -- not a failure.">
      Not configured
    </Badge>
  )
}

/* ---------------------------------------------------------------- ActorChip */

const ACTOR: Record<ActorType, { icon: React.ComponentType<{ className?: string }>; className: string; title: string }> =
  {
    SYSTEM: { icon: Zap, className: "text-slate-600 bg-slate-100", title: "Deterministic system logic." },
    AGENT: {
      icon: Bot,
      className: "text-violet-700 bg-violet-50",
      title: "Written by a language model. Never a decision -- an explanation.",
    },
    HUMAN: { icon: User, className: "text-blue-700 bg-blue-50", title: "A person decided this." },
  }

export function ActorChip({ actor, id }: { actor: ActorType; id?: string | null }) {
  const meta = ACTOR[actor] ?? ACTOR.SYSTEM
  const Icon = meta.icon
  return (
    <span
      title={meta.title}
      className={cn("inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium", meta.className)}
    >
      <Icon className="h-3 w-3" />
      {id ?? actor}
    </span>
  )
}

/* ------------------------------------------------------------ EvidenceCard */

export function EvidenceCard({ evidence }: { evidence: Evidence }) {
  return (
    <Card>
      <CardContent className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] text-muted-foreground">#{evidence.id}</span>
          <Badge variant="outline">{humanize(evidence.evidence_type)}</Badge>
          {/* Tier, always. See the module docstring. */}
          <TierBadge tier={evidence.source_tier} />
          <span className="ml-auto font-mono text-[10px] text-muted-foreground">
            conf {evidence.confidence.toFixed(2)}
          </span>
        </div>
        <p className="mt-2 text-sm">{evidence.extracted_fact}</p>
        {evidence.snippet ? (
          // Third-party text. Visually quarantined so a reader can see at a
          // glance that this is untrusted source material, not our finding.
          <blockquote className="mt-2 border-l-2 border-muted bg-muted/40 p-2 font-mono text-[11px] text-muted-foreground">
            {evidence.snippet.slice(0, 400)}
            {evidence.snippet.length > 400 ? "..." : ""}
          </blockquote>
        ) : null}
        <p className="mt-2 text-[10px] text-muted-foreground">
          {evidence.source_dataset}
          {evidence.provider_name ? ` - ${evidence.provider_name}` : ""} - {fmtDateTime(evidence.created_at)}
        </p>
      </CardContent>
    </Card>
  )
}

/* --------------------------------------------------------------- AlertCard */

export function AlertCard({ alert }: { alert: Alert }) {
  return (
    <Card>
      <CardContent className="flex items-start gap-3 p-3">
        <ShieldAlert
          className={cn(
            "mt-0.5 h-4 w-4 shrink-0",
            alert.severity === "CRITICAL" || alert.severity === "HIGH" ? "text-destructive" : "text-amber-600",
          )}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <RiskBadge band={alert.severity} />
            <Badge variant="outline">{humanize(alert.trigger)}</Badge>
            <Badge variant="muted">{humanize(alert.status)}</Badge>
          </div>
          <p className="mt-1 truncate text-sm">{alert.reason ?? "No reason recorded."}</p>
          <p className="mt-1 text-[10px] text-muted-foreground">
            Client {alert.client_id} - {fmtDateTime(alert.opened_at)}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

/* ---------------------------------------------------------------- CaseCard */

export function CaseCard({ item }: { item: CaseSummary }) {
  return (
    <Link
      to={`/cases/${item.id}`}
      className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <Card className="transition-colors hover:border-primary/40">
        <CardContent className="p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="font-mono text-xs font-semibold">{item.case_ref}</span>
            <StatusChip status={item.status} />
          </div>
          <p className="mt-1 truncate text-sm font-medium">{item.client_name}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <RiskBadge
              band={item.current_risk_band}
              score={item.current_risk_score}
              neverMonitored={item.current_risk_score === null}
            />
            {item.open_alert_count > 0 ? (
              <Badge variant="warning">{item.open_alert_count} open alert(s)</Badge>
            ) : null}
            {item.has_sar_draft ? <Badge variant="secondary">SAR draft</Badge> : null}
          </div>
          <p className="mt-2 text-[10px] text-muted-foreground">
            Opened {fmtDateTime(item.opened_at)}
            {item.assigned_to ? ` - ${item.assigned_to}` : " - unassigned"}
          </p>
        </CardContent>
      </Card>
    </Link>
  )
}

/* ------------------------------------------------------------ TimelineItem */

const TIMELINE_ICON: Record<TimelineEntryType, React.ComponentType<{ className?: string }>> = {
  MONITORING: Radar,
  PROVIDER_RESULT: CircleSlash,
  ENTITY_RESOLUTION: Search,
  EVIDENCE: FileText,
  RISK_EVENT: AlertTriangle,
  RISK_SCORE_CHANGE: Zap,
  ALERT: ShieldAlert,
  INVESTIGATION: Bot,
  HUMAN_REVIEW: Gavel,
  SAR: FileText,
}

export function TimelineItem({ entry, last }: { entry: TimelineEntry; last?: boolean }) {
  const Icon = TIMELINE_ICON[entry.entry_type] ?? Clock
  const tier = entry.metadata?.source_tier as string | undefined
  return (
    <li className="relative flex gap-3 pb-4">
      {!last ? <span className="absolute left-[13px] top-7 h-full w-px bg-border" aria-hidden="true" /> : null}
      <span className="z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border bg-card">
        <Icon className="h-3.5 w-3.5 text-muted-foreground" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{humanize(entry.entry_type)}</Badge>
          <ActorChip actor={entry.actor_type} id={entry.actor_id} />
          {tier ? <TierBadge tier={tier} /> : null}
          <time className="ml-auto font-mono text-[10px] text-muted-foreground" dateTime={entry.timestamp}>
            {fmtDateTime(entry.timestamp)}
          </time>
        </div>
        <p className="mt-1 text-sm font-medium">{entry.title}</p>
        {entry.summary ? <p className="mt-0.5 text-xs text-muted-foreground">{entry.summary}</p> : null}
        {entry.related_evidence_ids.length > 0 ? (
          <p className="mt-1 font-mono text-[10px] text-muted-foreground">
            evidence: {entry.related_evidence_ids.join(", ")}
          </p>
        ) : null}
      </div>
    </li>
  )
}

/* ---------------------------------------------------------------- AuditRow */

export function AuditRow({ entry }: { entry: import("@/api/types").AuditEntry }) {
  return (
    <tr className="border-b align-top hover:bg-muted/40">
      <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px] text-muted-foreground">
        {fmtDateTime(entry.created_at)}
      </td>
      <td className="px-3 py-2">
        <ActorChip actor={entry.actor_type} id={entry.actor_id} />
      </td>
      <td className="px-3 py-2 font-mono text-xs">{entry.action}</td>
      <td className="px-3 py-2 text-xs text-muted-foreground">
        {entry.target_type ? `${entry.target_type} ${entry.target_id ?? ""}` : "--"}
      </td>
      <td className="max-w-[24rem] px-3 py-2 text-xs text-muted-foreground">
        {entry.reason ?? "--"}
        {entry.old_value || entry.new_value ? (
          <div className="mt-1 font-mono text-[10px]">
            {entry.old_value ? <div className="truncate">- {entry.old_value}</div> : null}
            {entry.new_value ? <div className="truncate text-emerald-700">+ {entry.new_value}</div> : null}
          </div>
        ) : null}
      </td>
      <td className="px-3 py-2 font-mono text-[10px] text-muted-foreground">{entry.correlation_id ?? "--"}</td>
    </tr>
  )
}

/* ------------------------------------------------------- GroundingIndicator */

export function GroundingIndicator({
  passed,
  hallucinated,
}: {
  passed?: boolean | null
  hallucinated?: number | null
}) {
  if (passed === null || passed === undefined) return <Badge variant="muted">Not checked</Badge>
  return passed ? (
    <Badge variant="success" title="Every cited evidence id exists. Verified deterministically, not by a model.">
      <CheckCircle2 className="mr-1 h-3 w-3" />
      Grounded
    </Badge>
  ) : (
    <Badge
      variant="destructive"
      title="The model cited evidence that does not exist. Treat this report as unreliable."
    >
      <XCircle className="mr-1 h-3 w-3" />
      {hallucinated ?? 0} fabricated citation(s)
    </Badge>
  )
}

export { Building2 }
