/**
 * Public landing page -- the front door before the console.
 *
 * Everything here markets a REAL capability of the running system. There are no
 * invented metrics: the portfolio numbers are fetched live from the same API
 * the dashboard uses, and if the backend is not running they are shown as
 * unavailable rather than filled with a plausible-looking lie. The one claim
 * this product most wants to make -- "AI explains, humans decide" -- is the one
 * it can actually back up, so it leads with it.
 */

import { Link } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowRight,
  Bot,
  FileText,
  Gauge,
  Network,
  ScrollText,
  ShieldCheck,
  Sparkles,
} from "lucide-react"
import { api } from "@/api/client"
import { Button } from "@/components/ui"
import { useSession } from "@/lib/session"

function useLivePortfolio() {
  // Best-effort: the landing page must render with or without a backend, so a
  // failure resolves to null and the UI degrades to "live count unavailable"
  // instead of erroring or, worse, inventing a number.
  const total = useQuery({
    queryKey: ["landing", "customers", "total"],
    queryFn: () => api.countCustomers({}),
    retry: false,
    staleTime: 60_000,
  })
  const high = useQuery({
    queryKey: ["landing", "customers", "high"],
    queryFn: () => api.countCustomers({ sector_risk: "High" }),
    retry: false,
    staleTime: 60_000,
  })
  const alerts = useQuery({
    queryKey: ["landing", "alerts"],
    queryFn: () => api.alerts({ limit: 1 }),
    retry: false,
    staleTime: 60_000,
  })
  return { total, high, alerts }
}

const FEATURES = [
  {
    icon: Gauge,
    title: "Deterministic risk scoring",
    body: "The authoritative score is computed by config-driven code, not a model. Every point traces to a named factor and its evidence. No LLM ever sets a number.",
  },
  {
    icon: ShieldCheck,
    title: "Screening against real lists",
    body: "Entity resolution runs against the actual OFAC SDN files and OpenSanctions, with tier provenance that never lets a curated demo hit pose as a real regulatory one.",
  },
  {
    icon: Bot,
    title: "Autonomous investigation",
    body: "One AI step, and only one. The agent reads stored context and writes a grounded, cited report -- fabricated citations are flagged, not hidden -- then hands it to a human.",
  },
  {
    icon: Network,
    title: "Entity resolution",
    body: "False positives are cut by resolving names to entities with confidence bands. The engine can propose a match; only a human can confirm one.",
  },
  {
    icon: FileText,
    title: "Draft SAR, human sign-off",
    body: "The system assembles a Suspicious Activity Report as a DRAFT and never files it. Approving it is an attributed human decision, recorded in the audit trail.",
  },
  {
    icon: ScrollText,
    title: "Full audit trail",
    body: "Every monitoring cycle, risk event, investigation and human decision is append-only and timestamped. Closed cases never reopen -- the record is the point.",
  },
]

function Stat({ label, value, loading, unavailable }: { label: string; value?: number; loading: boolean; unavailable: boolean }) {
  return (
    <div className="rounded-lg border bg-card/60 px-4 py-3 text-center">
      <div className="text-2xl font-semibold tabular-nums">
        {loading ? (
          <span className="text-muted-foreground">…</span>
        ) : unavailable ? (
          <span className="text-sm font-normal text-muted-foreground">unavailable</span>
        ) : (
          value?.toLocaleString()
        )}
      </div>
      <div className="mt-0.5 text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
    </div>
  )
}

export default function Landing() {
  const { session } = useSession()
  const { total, high, alerts } = useLivePortfolio()
  const backendDown = total.isError && high.isError && alerts.isError

  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-muted/30">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-6 w-6 text-primary" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold leading-tight">Continuous KYC</p>
            <p className="text-[10px] text-muted-foreground">Autonomous Auditor</p>
          </div>
        </div>
        <Button asChild size="sm">
          <Link to={session ? "/" : "/signin"}>
            {session ? "Open workspace" : "Sign in"} <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </Button>
      </header>

      <main className="mx-auto max-w-6xl px-6">
        {/* Hero */}
        <section className="py-14 text-center md:py-20">
          <span className="inline-flex items-center gap-1.5 rounded-full border bg-card px-3 py-1 text-[11px] font-medium text-muted-foreground">
            <Sparkles className="h-3 w-3 text-primary" /> AI explains. Deterministic code scores. Humans decide.
          </span>
          <h1 className="mx-auto mt-5 max-w-3xl text-balance text-4xl font-bold tracking-tight md:text-5xl">
            Continuous KYC for high-risk corporate accounts
          </h1>
          <p className="mx-auto mt-4 max-w-2xl text-pretty text-base text-muted-foreground md:text-lg">
            Monitor exposure, cut false positives with entity resolution, investigate triggers with a grounded AI
            agent, and draft a SAR for human sign-off — with a full audit trail behind every step.
          </p>
          <div className="mt-7 flex flex-wrap items-center justify-center gap-3">
            <Button asChild size="lg">
              <Link to={session ? "/" : "/signin"}>
                {session ? `Continue as ${session.name}` : "Sign in to the console"}{" "}
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
            <Button asChild variant="outline" size="lg">
              <a href="#how">See how it works</a>
            </Button>
          </div>

          {/* Live portfolio — real numbers or an honest "unavailable" */}
          <div className="mx-auto mt-10 grid max-w-2xl grid-cols-3 gap-3">
            <Stat label="Clients monitored" value={total.data?.count} loading={total.isLoading} unavailable={total.isError} />
            <Stat label="High-risk sector" value={high.data?.count} loading={high.isLoading} unavailable={high.isError} />
            <Stat label="Open alerts" value={alerts.data?.total} loading={alerts.isLoading} unavailable={alerts.isError} />
          </div>
          {backendDown ? (
            <p className="mt-3 text-xs text-muted-foreground">
              Live portfolio figures are unavailable — the API at <code>:8000</code> is not responding. The numbers
              above populate from real ingested data once the backend is running.
            </p>
          ) : (
            <p className="mt-3 text-xs text-muted-foreground">Figures fetched live from the running API — not placeholders.</p>
          )}
        </section>

        {/* Features */}
        <section id="how" className="border-t py-14">
          <h2 className="text-center text-2xl font-semibold tracking-tight">What it actually does</h2>
          <p className="mx-auto mt-2 max-w-2xl text-center text-sm text-muted-foreground">
            Six real capabilities. The boundary is deliberate and enforced in code: agents detect, classify and
            explain; deterministic logic computes the score and enforces the workflow; a human makes the call.
          </p>
          <div className="mt-8 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <div key={f.title} className="rounded-lg border bg-card p-4">
                <f.icon className="h-5 w-5 text-primary" aria-hidden="true" />
                <h3 className="mt-2 text-sm font-semibold">{f.title}</h3>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{f.body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Honesty statement */}
        <section className="border-t py-14">
          <div className="mx-auto max-w-3xl rounded-xl border bg-card p-6 text-center">
            <ShieldCheck className="mx-auto h-7 w-7 text-primary" />
            <h2 className="mt-3 text-xl font-semibold">Built to be checked, not just believed</h2>
            <p className="mx-auto mt-2 max-w-2xl text-sm leading-relaxed text-muted-foreground">
              No fabricated data, sources, or findings. Where the dataset cannot support a feature, the gap is
              documented rather than faked — a real client produces no high-confidence sanctions match because the
              client master has no identifier to corroborate, and the system says so instead of inventing one. A live
              prompt-injection fixture sits in the evidence and fails to move the score, because no model ever sets it.
            </p>
          </div>
        </section>
      </main>

      <footer className="mx-auto max-w-6xl px-6 py-8 text-center text-[11px] text-muted-foreground">
        Deterministic scoring · AI explains, never decides · Humans approve · Full audit trail
      </footer>
    </div>
  )
}
