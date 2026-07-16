# Demo Script — one customer, end to end

A 5-minute walkthrough that traces a single customer through the whole system,
showing what changes at each step and who (or what) caused it. Every number
below is real, produced by running the steps described — nothing is staged.

**Before you start:** point the frontend at the backend that has the live API
keys loaded (see "Providers" below for why):

```bash
cd frontend
BACKEND_URL=http://localhost:8001 npm run dev      # if the backend is on 8001
# or just `npm run dev` if the backend is on 8000
```

The demo subject is **Wilson, Garcia and Daniels (client 44)** — an NGO operating
in real estate, flagged as a politically exposed person (PEP). A good subject
because it is a *real, clean* customer: the system will score it honestly, and
the AI will refuse to invent findings it cannot support. Any client works; this
one tells a clear story.

---

## The one line to open with

> "AI detects, classifies, investigates, and explains. **Deterministic code
> computes the risk score and enforces the workflow. A human makes the final
> call.** No language model ever sets a number."

Everything below demonstrates that sentence.

---

## Step 1 — The customer, before anything runs

Open **Customers → Wilson, Garcia and Daniels**.

- Profile: NGO, US, Real Estate (Medium-risk sector), **PEP flag = true**.
- Risk: **"Not assessed — never scored."**

Talking point: the risk panel is empty and *says why*. The platform never shows
a made-up score. "Not assessed" and "low risk" are different claims, and it
makes only the one it can defend.

## Step 2 — Monitor: the deterministic engine scores it

Open a case (or use the monitoring action) to run one cycle. The score appears:

```
SCORE: 15 / 100  ->  LOW
Driven by:
  +15  Politically exposed person     [MEDIUM]   (15 x 1.00 confidence)
   +0  Entity resolution conflict     [LOW]      candidate 'SOKOLOV, Dmitri' — not corroborated
```

Talking points:
- **Every point traces to a named factor.** The score is 15 because of one
  thing: the PEP flag. You can read the arithmetic.
- The engine surfaced a sanctions candidate ("SOKOLOV") and scored it **0** —
  the name did not corroborate, so it does not inflate the score. This is the
  false-positive control working: a near-name-match is not a hit.
- This is config-driven code (`risk_factors.json`), not a model. Same input,
  same score, every time.

## Step 3 — Live screening (the live external APIs)

Run **"Run live screening"** on the customer. This is the deliberate, opt-in
run that also calls the **live** newsdata.io and OpenSanctions APIs.

- `newsdata_adverse_media_api` fires — real, current news is queried for the name.
- OpenSanctions is checked during resolution and comes back **clean** for this
  customer (correct — they are not a sanctioned entity).

Talking point: this is real, live third-party data, tagged `EXTERNAL_LIVE` so it
can never be confused with the curated demo lists. OpenSanctions is a 50/month
trial, so it is gated to fire only on this explicit action — never on a bulk
sweep. That gate is in code, not a policy.

> To show a **positive** sanctions hit live, use entity resolution on a known
> name (e.g. resolve "Vladimir Putin") — the OpenSanctions API returns a
> HIGH_CONFIDENCE match with real DOB, country, and the lists it appears on.
> That is the same live API, shown against a name that is actually sanctioned.

## Step 4 — Open a case

A case (e.g. **CASE-000006**) is created in state `OPEN`. It aggregates the
customer's live risk, events, evidence, and matches — nothing is copied onto it,
so nothing on it can go stale.

## Step 5 — The autonomous investigation (the one LLM call)

Run the investigation. The agent (Groq `openai/gpt-oss-120b`) reads the stored
context and returns in ~3.6s:

```
status:  AWAITING_HUMAN_REVIEW
grounding: passed, 0 hallucinated citations
summary:  "The investigation file contains no evidential records ... therefore
           no factual findings can be substantiated."
```

Talking points — this is the most important moment:
- The agent **refused to fabricate.** With no evidence on file, it said so and
  produced procedural recommendations instead of inventing findings. That is the
  anti-hallucination design surviving contact with a real model.
- It **never closes itself** — status is `AWAITING_HUMAN_REVIEW`. The LLM
  explains; it does not decide.
- Deterministic grounding validation checked every citation; a fabricated one
  would be flagged and shown, not hidden.

## Step 6 — Everything is on the record

Open the case's **Audit trail**:

```
[AGENT ] investigation_run     by investigation_agent:groq:openai/gpt-oss-120b
[SYSTEM] case_opened           by case_service
[SYSTEM] monitoring_cycle      by monitoring_service
[SYSTEM] monitoring_cycle      by monitoring_service
```

Talking point: every action is append-only and attributed to **AGENT**,
**SYSTEM**, or **HUMAN** — and the trail never lets those be confused. The one
row missing here is a HUMAN one, which is the next step: you.

## Step 7 — The human decides

In the case workspace, enter your name (the sign-in captured it) and take an
action: acknowledge, escalate, request information, or close. The backend
**rejects an unattributed decision** — a compliance decision without a name is
not a compliance decision. If a SAR is warranted, the system assembles a
**DRAFT** and never files it; approving it is your attributed, audited decision.

---

## The contrast that sells it

Run the same steps on **Phillips-Hanson (client 3)**: it scores **70.6 / HIGH**
(upstream sanctions flag + high-risk sector + ownership opacity), raises alerts,
and carries a full investigation and a draft SAR. Same engine, same rules, a very
different customer — and the difference is entirely explained by the factors, not
by anyone's judgement. Show the clean customer and the high-risk one side by
side: the system treats them differently for reasons you can read.

---

## Providers: why "Ready" vs "Not configured"

On the dashboard, `local_*` and `tier1_*` providers are **Ready** (they read
local Tier-1 data — real OFAC files, etc.). The live API providers show:

- **Ready** — `newsdata_adverse_media_api`, `opensanctions_match_api` when the
  keys are loaded (backend on the port your frontend points at).
- **Not configured** — the `pending_*` placeholders, shown only when the backend
  has no keys. If you see these, your frontend is pointed at a keyless backend;
  restart it with `BACKEND_URL=http://localhost:8001` (or run the backend on the
  port the frontend proxies to).

"Not configured" is honest, not broken: it means "no credentials, this provider
was never expected to answer." It is never dressed up as a successful check.
