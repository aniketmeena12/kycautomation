# Phase 7 -- Enterprise Frontend

**Continuous KYC Autonomous Auditor**
**Status:** complete. React + Vite + TypeScript + Tailwind + shadcn-style
components + TanStack Query + React Router + Recharts + Lucide. No backend
change of any kind.

---

## 1. Architecture

```
frontend/src/
  api/
    types.ts      hand-written types mirroring the REAL OpenAPI schema
    client.ts     the SINGLE HTTP boundary + typed ApiError
  hooks/
    queries.ts    one TanStack Query hook per real endpoint
  components/
    ui/           shadcn-style primitives (Button, Card, Badge, Table, ...)
    domain/       StatCard, RiskBadge, TierBadge, EvidenceCard, AlertCard,
                  CaseCard, TimelineItem, AuditRow, ProviderBadge, StatusChip,
                  ActorChip, GroundingIndicator
  pages/          9 pages, all lazy-loaded
  lib/utils.ts    cn(), date/number formatting
  test/           vitest + Testing Library
```

**One HTTP boundary.** Every network call goes through `request()` in
`api/client.ts`. That is what makes "no mock data" *checkable* rather than
aspirational: there is exactly one place a fabricated response could be
injected, and it isn't.

**Types are written against the live schema, not the wish-list.** Where the UI
wants something the API does not serve, the type does not invent it and the page
shows an honest empty state.

---

## 2. Pages

| # | Route | Page | Endpoints consumed |
|---|---|---|---|
| 1 | `/` | Executive Dashboard | `/cases/metrics`, `/customers` (×6 aggregate probes), `/alerts` (×5), `/providers`, `/datasets/status`, `/investigations/agent/status`, `/health/ready`, `/cases` |
| 2 | `/customers` | Customer Explorer | `/customers` |
| 3 | `/customers/:id` | Customer 360 | `/customers/{id}/360`, `/risk/{id}`, `/risk/history/{id}`, `/events/{id}`, `/investigations/client/{id}`, `/alerts`, `POST /cases` |
| 4 | `/investigations/:id` | Investigation Workspace | `/investigations/{id}` |
| 5 | `/timeline/:caseId?` | Timeline | `/cases`, `/cases/{id}/timeline` |
| 6 | `/cases`, `/cases/:id` | Case Management | `/cases`, `/cases/metrics`, `/cases/{id}`, `POST /cases/{id}/review`, `POST /cases/{id}/sar` |
| 7 | `/sar/:caseId?` | Draft SAR Viewer | `/cases/{id}/sar`, `/cases/{id}`, `POST /cases/{id}/review` |
| 8 | `/audit/:caseId?` | Audit Trail | `/cases/{id}/audit` |
| 9 | `/system` | System Health | `/health/ready`, `/providers`, `/datasets/status`, `/cases/metrics`, `/investigations/agent/status` |

**20 distinct backend endpoints consumed**, all verified returning 200 against a
live server with real ingested data.

---

## 3. The honesty rules the UI enforces

The backend went to considerable lengths to distinguish facts that look alike. A
UI is a presentation, so it can undo all of it. These are the rules that stop it:

| Backend guarantee | How the UI honours it |
|---|---|
| `never_monitored` + `null` score (never 0/LOW) | `RiskBadge` renders **"Not assessed"**. Tested. |
| Tier 2 is demo data, never authoritative (ADR-002) | `TierBadge` renders on every evidence item and timeline entry, with a tooltip saying **NOT authoritative**. Tested. |
| `null` ≠ `0` (latency, laundering label) | `fmtOrDash` renders `--`; `0` renders `0`. Tested. |
| SYSTEM / AGENT / HUMAN are different (Phase 6) | `ActorChip` gives each a distinct icon and colour. |
| A fabricated citation must be loud (ADR-028) | `GroundingIndicator` shows it **before** the prose, plus a page-level alert. Tested. |
| `NOT_CONFIGURED` is not a failure (Phase 4 §6) | `ProviderBadge` renders it neutral with "never expected to answer". |
| The agent never decides | Investigation page states it; SAR/review actions live on the case and require a named reviewer. |
| Only legal actions (Phase 6 state machine) | The review form is driven **entirely** by `available_actions` from the server. |
| Reviews are append-only | Reviewer history is a list, never an edit form. |
| A SAR is always DRAFT | Fixed watermark + per-section attribution + "this system never files". |

---

## 4. Charts (Recharts)

| Chart | Page | Source |
|---|---|---|
| Portfolio risk distribution (pie) | Dashboard | 3 × `/customers?sector_risk=X&limit=1` → `total` |
| Alert severity mix (bar) | Dashboard | 4 × `/alerts?severity=X&limit=1` → `total` |
| Case status (horizontal bar) | Dashboard | `/cases/metrics` |
| Risk history (step line) | Customer 360 | `/risk/history/{id}` |

**The aggregate trick, and its honest limit.** The backend has no aggregation
endpoints. Rather than fetch 2,000 customers to count them, the dashboard issues
`limit=1` probes and reads `total` — a real server-side count over the whole
portfolio in one tiny request. That works for anything the API can *filter* by.

It does **not** work for computed risk band, because `/customers` cannot filter
on it and `ClientRead` carries no score. Deriving that chart would mean one
`/risk/{id}` call per client — 2,000 requests to draw one pie. So the chart shows
the **sector-risk label from the client master** and says so, in the card, in
words. It is not the engine's computed band and is never labelled as such.

---

## 5. State management

TanStack Query only. No Redux, no Zustand, no context store — server state is
the only state this app has, and a second copy of it would be a second source of
truth (the same reasoning as Phase 6's ADR-032).

Caching is deliberate:
- **Reference data** (providers, risk factors, sources): 30-minute `staleTime`.
- **Operational data** (cases, alerts, investigations): 30s, fresh on mount.
- **`refetchOnWindowFocus` is OFF globally.** A reviewer tabbing back to a case
  must not have the page shift under them mid-decision; a silent refetch that
  reorders a queue while someone is clicking it is worse than slightly stale data.
- **Nothing polls.** There is no live feed, and an interval against single-writer
  SQLite (ADR-001) would be load with no signal.
- **404 is never retried** — across this API it routinely means a legitimate
  empty state ("no SAR draft yet"), not a transient failure.

Mutations invalidate the case aggregate *and* `/cases/metrics`: a review changes
both, and leaving the dashboard tile stale would show a number the reviewer just
disproved.

---

## 6. Accessibility

Skip-to-content link; semantic landmarks (`nav` + `main`); visible focus ring on
every interactive element (`:focus-visible` in the base layer, so a page cannot
forget it); `aria-pressed` on filter toggles; `aria-sort` on sortable headers;
`role="status"` + `aria-busy` + `sr-only` label on every loading region (a bare
pulsing box is invisible to a screen reader); `role="alert"` on errors; native
`<select>` for all dropdowns — keyboard- and screen-reader-correct by default,
and a hand-rolled listbox would be more code and less accessible.

Contrast: slate-on-white body text and the risk palette are chosen for AA at
normal weight.

---

## 7. Responsiveness

Desktop-first. Sidebar collapses below `md:` into a horizontal scroller rather
than a hamburger drawer — every destination stays one tap away and there is no
second navigation model to keep in sync. Grids step 6→3→2 columns; tables scroll
horizontally inside their own container so the page body never does.

---

## 8. Tests

**21 tests, all passing, ~6s** (`npm test`).

Coverage: null-vs-zero formatting; `RiskBadge` never rendering 0/LOW for an
unscored client; `TierBadge` marking demo data non-authoritative;
`GroundingIndicator` surfacing fabricated citations; loading regions announcing
themselves; errors preserving the backend's own actionable detail; `ApiError`
404/409 classification; routing + real API integration on the Cases page;
error state when the backend is unreachable; **empty state rather than an error**
when there are no cases; server-side status filtering hitting the right query
param; and the SAR viewer showing an empty state (not an error) on a 404.

`fetch` is stubbed — that is test doubling, not mock data in the product. It lets
the tests assert behaviour against responses a live backend could not be made to
produce on demand (a fabricated citation, an unreachable API).

**Two real findings from writing them:**
1. Vitest's default `forks` pool times out on this repo's path (it contains a
   space: `ds project`). Pinned to `pool: "threads"` in `vite.config.ts`.
2. The query hooks set `retry` explicitly, which **overrides** a test client's
   `retry: false` (hook options beat client defaults in TanStack Query). The
   error-state tests therefore allow for the real backoff instead of assuming
   none — the test was wrong, not the code.

---

## 9. Validation performed

- `npx tsc --noEmit` — clean.
- `npx vite build` — succeeds; per-page code splitting confirmed (Dashboard
  425 kB, every other page 1.5–28 kB).
- `npm test` — 21/21.
- Live backend, real ingested data (2,000 clients): all **20** endpoints the UI
  consumes return **200**. Seeded workflow verified end-to-end:
  `monitor → 53.0 HIGH` → `case CASE-000001 OPEN` → `review 200` → `sar 201`
  → timeline generates **7 entries across 5 types**.

### A real backend defect this found

`POST /cases` returned **500: `no such column: human_reviews.case_id`** against
a database created before Phase 6.

`init_db.py` uses `Base.metadata.create_all()`, which creates missing **tables**
but never adds **columns** to existing ones. Phase 6 added `case_id`,
`previous_state`, and `new_state` to the pre-existing `human_reviews` table, so
any database created before Phase 6 is silently missing them. **The test suite
cannot catch this** — it builds a fresh database every run, where `create_all`
produces every column.

This is a Phase 6 migration gap, not a frontend bug, and fixing it properly means
a migration tool (Alembic), which Phase 7 is explicitly scoped out of. Recorded
as a trap in `CLAUDE.md` with the workaround (recreate the dev DB; data
re-ingests in ~45s) and the real fix (adopt Alembic) as the recommended next step.

---

## 10. Limitations

1. **Customer search, country filter, and sort are CLIENT-SIDE** and apply only
   to the loaded page. `/customers` accepts only
   `limit/offset/sanctions_flag/pep_flag/sector_risk/mapped_only`. The page says
   so in a banner — a page-scoped search that *looked* global would be the most
   misleading thing here: a compliance officer would conclude a customer does not
   exist. **Fix: add `q`, `country`, and `sort` to `/customers`.**
2. **Risk distribution is the sector-risk label, not the computed band** (§4).
   **Fix: a `/risk/distribution` aggregate.**
3. **The audit page is case-scoped.** There is no global `/audit` endpoint, so it
   cannot show system-wide activity. **Fix: `GET /audit` with filters.**
4. **API latency is measured in the browser**, not server-side APM. Labelled as
   such on the page.
5. **No alert-trend-over-time chart.** `/alerts` returns `opened_at` but only the
   loaded page; a real trend needs a time-bucketed aggregate.
6. **SAR "Edit" is local-only.** The backend freezes a draft at generation by
   design and exposes no update endpoint. The UI labels the notes as
   browser-local and directs durable decisions to Approve/Reject.
7. **PDF export is `window.print()`** against a print stylesheet, not a PDF
   library — the browser's engine produces a correct, selectable, paginated PDF
   for one fewer dependency.
8. **No authentication.** `reviewer` is a free-text field, inheriting Phase 6's
   headline gap: the system records who *claimed* to decide.
9. **Dashboard issues 10 aggregate probes on load.** Cheap (`limit=1`) and
   cached, but a single `/metrics/portfolio` endpoint would replace all of them.
10. **shadcn primitives are hand-written** in-repo (the CLI needs an interactive
    session) and consolidated into `components/ui/index.tsx` rather than one file
    per component.

---

## 11. Running it

```bash
# backend (terminal 1)
cd backend && python -m uvicorn app.main:app --port 8000

# frontend (terminal 2)
cd frontend && npm install && npm run dev   # http://localhost:5173
```

Vite proxies `/api` and `/health` to `localhost:8000`, so the app is same-origin
in dev and needs no CORS and no hardcoded host. `VITE_API_URL` overrides for a
split-origin deployment.

If the portfolio is empty, ingest first:
`curl -X POST localhost:8000/api/v1/ingestion/load -H 'Content-Type: application/json' -d '{"all":true}'`
