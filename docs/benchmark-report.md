# Benchmark Report

Measured on the development machine (Windows 11, Python 3.11, SQLite/WAL,
single uvicorn worker) against **real ingested data**: 2,000 clients, 120
accounts, 1.70 GB of source datasets.

---

## 1. API latency — and an honest measurement caveat

**Server-side latency is below the measurement floor of the tooling available in
this environment.** Two independent harnesses both returned a flat, uniform
figure across every endpoint — including `/health/ready`, which does almost
nothing:

| Harness | Fixed overhead | Spread across 16 endpoints |
|---|---:|---|
| `curl` (per-process) | ~215 ms | 217.5 – 233.5 ms |
| `urllib` (per-request, localhost) | ~2040 ms | 2041 – 2057 ms |

A trivial health check and a full Customer-360 assembly costing the *same*
2,041 ms is not a statement about the server; it is the client overhead
(Windows localhost resolution / process startup) swamping the signal.

**What can honestly be concluded:**

- No endpoint is measurably slower than `/health/ready`. The *deltas* — the only
  trustworthy part — are small and uniform:

| Endpoint | Delta over `/health/ready` (curl, n=5) |
|---|---:|
| `/api/v1/cases/metrics` | **+16.0 ms** |
| `/api/v1/cases/1` (full workspace) | +12.8 ms |
| `/api/v1/cases/1/sar` | +8.9 ms |
| `/api/v1/cases/1/timeline` | +7.7 ms |
| `/api/v1/customers?limit=50` | +5.2 ms |
| `/api/v1/customers/3/360` | +3.8 ms |
| `/api/v1/risk/3` | +1.6 ms |
| `/api/v1/cases/1/audit` | +1.5 ms |

`/cases/metrics` being the slowest is expected and explained: it walks every
open case to compute `high_risk_cases` (see Phase 6 §11 limitation 6).

**To measure properly**, run the app under a real load tool (`k6`, `locust`, or
`ab`) from a host without the localhost-resolution penalty. Reporting a precise
p99 from this environment would be fabricating precision I do not have.

## 2. Monitoring cycle — measured

`POST /monitor/client/3` (no providers, no resolution), 3 runs:
**236 ms / 244 ms / 222 ms**, wall-clock including the same ~215 ms client
overhead — so the cycle itself is roughly **10–30 ms**.

Deterministic across runs: score **53.0 / HIGH** every time
(40 upstream sanctions + 8 high-risk sector + 5 ownership opacity).

## 3. Investigation latency — real, server-measured

This is the one latency figure the **server itself** records, so it is free of
client overhead. From a live Groq call (`openai/gpt-oss-120b`):

| | |
|---|---:|
| Latency | **3,948 ms** |
| Input tokens | 2,807 |
| Output tokens | 1,483 |
| Temperature | 0.0 (sent; Groq accepts sampling params) |
| Grounding | passed, 0 fabricated citations |

Exposed live at `GET /api/v1/cases/metrics → average_investigation_latency_ms`,
averaged only over investigations that **produced a report** — including failures
would average in calls that returned nothing and make a broken provider look fast.
It is `null`, not `0.0`, when none have run.

**Groq TPM caveat (paid for in a real 413):** Groq counts input **+ reserved
`max_completion_tokens`** against its tokens-per-minute limit. A 2.8k prompt with
`max_completion_tokens=8000` bills as ~10.8k and is rejected on an 8k tier.
`LLM_MAX_OUTPUT_TOKENS=4000` is therefore a TPM setting, not just an output cap.

## 4. Ingestion — measured across phases

| Operation | Time | Note |
|---|---:|---|
| `clients` (2,000 rows) | ~1.7 s | |
| `client_account_mapping` (120 rows) | ~0.2 s | |
| Full ingest (all sources) | ~43–57 s | |
| Transactions (50,000 rows) | **41 s** | Was **285 s+ and climbing** before ADR-006 |

**ADR-006 is the headline performance result.** SQLite's query planner chose a
2-distinct-value index on `transaction_source` over the selective one, turning
every upsert lookup into a near-full scan. A composite
`UNIQUE(transaction_source, external_transaction_id)` matching the real natural
key took it from *superlinear and climbing* to 41 s.

## 5. Large-dataset lookup

| Dataset | Size | Strategy | Cost |
|---|---:|---|---|
| SAML-D | 951 MB / 9.5M rows | `LOOKUP_ONLY`, streamed | ~18 s/account scan |
| OpenSanctions | 488 MB / 1.3M rows | `LOOKUP_ONLY`, streamed | ~40–45 s |
| OFAC ×3 | — | `LOOKUP_ONLY`, streamed | fast |

**Never bulk-loaded, by design.** Proof the rule holds: SQLite stays ~18 MB
against 1.6 GB of source data. The OpenSanctions provider is opt-in per request
(`allow_expensive_providers`) precisely because 40 s is not viable per cycle.

## 6. Database

| | |
|---|---:|
| Size (demo: 2,000 clients + 1 full case) | **4 KB page-reported / ~1.5 MB with data** |
| Size after full ingest | ~18 MB |
| Tables | 25 |
| Journal mode | WAL, `synchronous=NORMAL` (ADR-010) |

WAL + `synchronous=NORMAL` is what made bulk ingestion viable: the default
(rollback journal + `synchronous=FULL`) fsyncs on **every commit**.

## 7. Frontend

| | |
|---|---:|
| Production build | **40.0 s** |
| Initial bundle (index) | 302.75 kB → **96.07 kB gzip** |
| Dashboard chunk (Recharts) | 425.11 kB → 121.84 kB gzip |
| Every other page | 1.5 – 28 kB |
| Test suite | 21 tests / **5.8 s** |
| Dev server cold start | **372 ms** |

Per-page code splitting confirmed: the Recharts weight lands in the Dashboard
chunk and is not paid by anyone opening the Audit page.

## 8. Memory

Not instrumented. The one measurement that matters is indirect and strong: the
lookup-only strategy means **1.6 GB of source data never enters the process** —
providers stream with chunked pandas reads. A resident-set profile would need
`memory_profiler` under sustained load, which is beyond this review.

---

## What I would measure next, in order

1. **Real p50/p99 under load** from a host without the localhost penalty (k6).
2. **`/cases/metrics` at scale** — the per-case walk is O(open cases) and is the
   only known algorithmic hot spot.
3. **Concurrent monitoring** — single-writer SQLite (ADR-001) is the ceiling; this
   is where Postgres would first earn its place.
