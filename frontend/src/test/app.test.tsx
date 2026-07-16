/**
 * Frontend tests.
 *
 * `fetch` is stubbed rather than a real backend hit: these assert what the UI
 * does with a KNOWN response, including responses a live backend could not be
 * made to produce on demand (a fabricated citation, an unconfigured agent).
 * That is test doubling, not mock data in the product -- the app itself has
 * exactly one HTTP boundary (src/api/client.ts) and no canned payloads.
 *
 * The assertions concentrate on the claims that matter: honest empty states,
 * never rendering 0/LOW for an unscored client, surfacing grounding failures,
 * and never offering an action the backend would reject.
 */

import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "@/api/client"
import { EmptyState, ErrorState, LoadingBlock } from "@/components/ui"
import { GroundingIndicator, RiskBadge, StatusChip, TierBadge } from "@/components/domain"
import { fmtOrDash, humanize } from "@/lib/utils"
import { SessionProvider } from "@/lib/session"

function wrap(ui: React.ReactNode, route = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  // SessionProvider is part of the app's real provider tree now that pages
  // attribute actions to a signed-in reviewer. Tests render with no stored
  // session (the default), which is the honest "not signed in" state -- pages
  // must still render, just with an empty reviewer field.
  return render(
    <QueryClientProvider client={client}>
      <SessionProvider>
        <MemoryRouter initialEntries={[route]}>{ui}</MemoryRouter>
      </SessionProvider>
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

/* ------------------------------------------------------------ formatting */

describe("null-vs-zero discipline", () => {
  it("renders null as a dash, never as 0", () => {
    // The backend deliberately returns null (not 0) for "nothing ran" and "no
    // label exists". Coercing to 0 in the UI would undo that.
    expect(fmtOrDash(null)).toBe("--")
    expect(fmtOrDash(undefined)).toBe("--")
    expect(fmtOrDash(0)).toBe("0")
    expect(fmtOrDash(42, " ms")).toBe("42 ms")
  })

  it("humanizes enum values", () => {
    expect(humanize("RISK_SCORE_CHANGE")).toBe("Risk score change")
    expect(humanize(null)).toBe("--")
  })
})

/* --------------------------------------------------------------- RiskBadge */

describe("RiskBadge", () => {
  it("shows 'Not assessed' for a never-monitored client rather than 0/LOW", () => {
    // The single most important honesty rule on the risk surface: "we assessed
    // them and they're fine" and "nobody ever looked" are opposite claims.
    render(<RiskBadge neverMonitored />)
    expect(screen.getByText("Not assessed")).toBeInTheDocument()
    expect(screen.queryByText("LOW")).not.toBeInTheDocument()
  })

  it("renders the band and score when assessed", () => {
    render(<RiskBadge band="HIGH" score={53} />)
    expect(screen.getByText("HIGH")).toBeInTheDocument()
    expect(screen.getByText("53")).toBeInTheDocument()
  })

  it("falls back to Not assessed when the band is missing", () => {
    render(<RiskBadge band={null} score={null} />)
    expect(screen.getByText("Not assessed")).toBeInTheDocument()
  })
})

/* --------------------------------------------------------------- TierBadge */

describe("TierBadge", () => {
  it("marks curated demo data as NOT authoritative", () => {
    // ADR-002: a Tier-2 hit must never be presentable as a real regulatory
    // finding -- and a UI is a presentation.
    render(<TierBadge tier="TIER_2_CURATED_DEMO" />)
    const el = screen.getByText("Tier 2 demo")
    expect(el).toBeInTheDocument()
    expect(el.getAttribute("title")).toMatch(/NOT authoritative/i)
  })

  it("distinguishes tier 1", () => {
    render(<TierBadge tier="TIER_1_AUTHORITATIVE" />)
    expect(screen.getByText("Tier 1")).toBeInTheDocument()
  })
})

/* ------------------------------------------------------ GroundingIndicator */

describe("GroundingIndicator", () => {
  it("surfaces fabricated citations loudly", () => {
    render(<GroundingIndicator passed={false} hallucinated={2} />)
    expect(screen.getByText(/2 fabricated citation/i)).toBeInTheDocument()
  })

  it("shows grounded when validation passed", () => {
    render(<GroundingIndicator passed />)
    expect(screen.getByText("Grounded")).toBeInTheDocument()
  })

  it("says 'Not checked' rather than implying success when unknown", () => {
    render(<GroundingIndicator passed={null} />)
    expect(screen.getByText("Not checked")).toBeInTheDocument()
  })
})

/* -------------------------------------------------------------- UI states */

describe("loading and error states", () => {
  it("announces loading to assistive tech", () => {
    render(<LoadingBlock label="Loading cases" />)
    const status = screen.getByRole("status")
    expect(status).toHaveAttribute("aria-busy", "true")
    expect(screen.getByText("Loading cases")).toBeInTheDocument()
  })

  it("renders an error as an alert and preserves the backend's own detail", () => {
    // The backend's messages are deliberately actionable; flattening them to
    // "Request failed" would throw away the most useful thing we received.
    render(<ErrorState title="Could not load" detail="Set LLM_API_KEY in backend/.env" />)
    expect(screen.getByRole("alert")).toBeInTheDocument()
    expect(screen.getByText(/Set LLM_API_KEY/)).toBeInTheDocument()
  })

  it("renders an empty state with guidance", () => {
    render(<EmptyState title="No evidence on file" description="An empty evidence base, not a finding of no risk." />)
    expect(screen.getByText("No evidence on file")).toBeInTheDocument()
    expect(screen.getByText(/not a finding of no risk/i)).toBeInTheDocument()
  })
})

/* -------------------------------------------------------------- ApiError */

describe("ApiError", () => {
  it("classifies 404 as a not-found rather than a generic failure", () => {
    // Across this API, 404 routinely means a legitimate empty state ("no SAR
    // draft yet"), which is why it is never retried and never shown as an error.
    const e = new ApiError(404, "No SAR draft exists", "/x")
    expect(e.isNotFound).toBe(true)
    expect(e.isConflict).toBe(false)
  })

  it("classifies 409 as a conflict (illegal state transition)", () => {
    const e = new ApiError(409, "Action ESCALATE is not permitted while the case is CLOSED", "/x")
    expect(e.isConflict).toBe(true)
    expect(e.message).toMatch(/not permitted/)
  })
})

/* ------------------------------------------------------------- StatusChip */

describe("StatusChip", () => {
  it("humanizes case status", () => {
    render(<StatusChip status="SAR_REVIEW" />)
    expect(screen.getByText("Sar review")).toBeInTheDocument()
  })
})

/* ---------------------------------------------------- routing + API wiring */

describe("Cases page (routing + API integration)", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (String(url).includes("/cases/metrics"))
          return new Response(
            JSON.stringify({
              open_cases: 1, under_review_cases: 0, escalated_cases: 0, sar_review_cases: 0,
              closed_cases: 0, total_cases: 1, high_risk_cases: 1, sar_pending: 0, sar_approved: 0,
              sar_rejected: 0, human_review_count: 0, human_reviews_by_action: {},
              average_investigation_latency_ms: null, investigations_total: 0, investigations_failed: 0,
              generated_at: new Date().toISOString(),
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          )
        return new Response(
          JSON.stringify({
            cases: [
              {
                id: 1, case_ref: "CASE-000001", client_id: 3, external_client_id: 3,
                client_name: "Phillips-Hanson", status: "OPEN", title: null, assigned_to: null,
                opened_at: new Date().toISOString(), closed_at: null,
                current_risk_score: 53, current_risk_band: "HIGH",
                open_alert_count: 1, investigation_count: 1, review_count: 0, has_sar_draft: false,
              },
            ],
            total: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }),
    )
  })

  it("renders real API data and links to the case", async () => {
    const { default: CasesPage } = await import("@/pages/Cases")
    wrap(
      <Routes>
        <Route path="/" element={<CasesPage />} />
      </Routes>,
    )
    expect(await screen.findByText("Phillips-Hanson")).toBeInTheDocument()
    expect(screen.getByText("CASE-000001")).toBeInTheDocument()
    expect(screen.getByText("HIGH")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /CASE-000001/ })).toHaveAttribute("href", "/cases/1")
  })

  it("shows an error state when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch") }))
    const { default: CasesPage } = await import("@/pages/Cases")
    wrap(
      <Routes>
        <Route path="/" element={<CasesPage />} />
      </Routes>,
    )
    // The query hooks set their own `retry`, which overrides the test client's
    // retry:false -- so allow for the real backoff before the error renders.
    await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument(), { timeout: 8000 })
    expect(screen.getByText(/Is the backend running/i)).toBeInTheDocument()
  })

  it("shows an empty state, not an error, when there are no cases", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        String(url).includes("metrics")
          ? new Response(JSON.stringify({ total_cases: 0, open_cases: 0, under_review_cases: 0, escalated_cases: 0, sar_review_cases: 0, closed_cases: 0, high_risk_cases: 0, sar_pending: 0, sar_approved: 0, sar_rejected: 0, human_review_count: 0, human_reviews_by_action: {}, average_investigation_latency_ms: null, investigations_total: 0, investigations_failed: 0, generated_at: new Date().toISOString() }), { status: 200, headers: { "Content-Type": "application/json" } })
          : new Response(JSON.stringify({ cases: [], total: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }),
      ),
    )
    const { default: CasesPage } = await import("@/pages/Cases")
    wrap(
      <Routes>
        <Route path="/" element={<CasesPage />} />
      </Routes>,
    )
    expect(await screen.findByText("No cases yet", {}, { timeout: 8000 })).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("filters by status via a server-side query parameter", async () => {
    const fetchSpy = vi.fn(async (url: string) =>
      String(url).includes("metrics")
        ? new Response(JSON.stringify({ total_cases: 0, open_cases: 0, under_review_cases: 0, escalated_cases: 0, sar_review_cases: 0, closed_cases: 0, high_risk_cases: 0, sar_pending: 0, sar_approved: 0, sar_rejected: 0, human_review_count: 0, human_reviews_by_action: {}, average_investigation_latency_ms: null, investigations_total: 0, investigations_failed: 0, generated_at: new Date().toISOString() }), { status: 200, headers: { "Content-Type": "application/json" } })
        : new Response(JSON.stringify({ cases: [], total: 0 }), { status: 200, headers: { "Content-Type": "application/json" } }),
    )
    vi.stubGlobal("fetch", fetchSpy)
    const { default: CasesPage } = await import("@/pages/Cases")
    wrap(
      <Routes>
        <Route path="/" element={<CasesPage />} />
      </Routes>,
    )
    await screen.findByText("No cases yet", {}, { timeout: 8000 })
    await userEvent.click(screen.getByRole("button", { name: /ESCALATED/i }))
    await waitFor(() =>
      expect(fetchSpy.mock.calls.some(([u]) => String(u).includes("status=ESCALATED"))).toBe(true),
    )
  })
})

/* ---------------------------------------------------- SAR viewer guarantees */

describe("Draft SAR viewer", () => {
  it("shows an empty state (not an error) when no draft exists yet", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        const u = String(url)
        if (u.includes("/sar"))
          return new Response(JSON.stringify({ detail: "No SAR draft exists for case 1." }), { status: 404, headers: { "Content-Type": "application/json" } })
        if (u.includes("/cases/1"))
          return new Response(JSON.stringify({ case: {}, available_actions: [] }), { status: 200, headers: { "Content-Type": "application/json" } })
        return new Response(
          JSON.stringify({
            cases: [{ id: 1, case_ref: "CASE-000001", client_id: 3, external_client_id: 3, client_name: "X", status: "OPEN", title: null, assigned_to: null, opened_at: new Date().toISOString(), closed_at: null, current_risk_score: null, current_risk_band: null, open_alert_count: 0, investigation_count: 0, review_count: 0, has_sar_draft: false }],
            total: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }),
    )
    const { default: SarPage } = await import("@/pages/Sar")
    wrap(
      <Routes>
        <Route path="/" element={<SarPage />} />
      </Routes>,
    )
    expect(await screen.findByText("No SAR draft for this case", {}, { timeout: 8000 })).toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })
})
