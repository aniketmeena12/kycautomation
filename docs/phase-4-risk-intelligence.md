# Phase 4 -- Continuous Monitoring & Explainable Risk Intelligence

**Continuous KYC Autonomous Auditor**
**Status:** complete. Turns the project from a static KYC lookup tool into an
event-driven Continuous KYC platform. No LLM investigation, timeline, SAR,
frontend, or human-review UI -- those remain future phases.

---

## 1. The monitoring cycle

```
Client
  └─▶ INTERNAL signals      profile flags, geography, sector, opacity, transaction typologies
  └─▶ RESOLUTION signals    Phase 3 entity resolution (reused wholesale)
  └─▶ PROVIDER signals      adverse media, in parallel, failure-tolerant
              │
              ▼
      CHANGE DETECTION       only unseen dedup_keys become RiskEvents
              │
              ▼
      RISK ENGINE            deterministic, config-driven, over ALL signals
              │
              ▼
      SNAPSHOT (append-only) + ALERTS (change-triggered)
```

**No LLM anywhere.** Every collector reads structured data or a provider
result. `tests/test_risk_engine.py` parses the engine module's AST and asserts
it imports no model SDK, HTTP client, or DB session -- the core design
principle enforced structurally, not by policy.

### Two subtleties that matter

**1. Scoring uses ALL signals; events use only NEW ones.**
A client's risk is a function of everything currently true about them, not of
what changed this cycle. If scoring only saw new signals, a client's score
would collapse to ~0 on the second run when nothing changed. Change detection
governs *event and alert creation*; scoring is stateless over the full current
picture. Verified: cycle 2 on unchanged data yields `new_events=0,
suppressed=3`, and the score stays identical at 53.0.

**2. Alerts key on CHANGE, never on state.**
A client sitting at CRITICAL for a month with nothing new must not re-alert
every cycle -- that is how alert fatigue starts, and a queue nobody reads is
worse than no queue.

---

## 2. Risk Factor Registry (configuration, not code)

`backend/config/risk_factors.json`, loaded and strictly validated by
`app/risk/config.py`. **Adding a factor requires no code change** -- append a
JSON object; the engine discovers it by matching `trigger_condition` against
incoming signals. Proven by
`test_new_factor_needs_no_code_change`, which invents a factor with a
never-before-seen `signal_type` and gets a correct score from it.

Each factor carries exactly what the brief specifies: `id`, `name`,
`description`, `weight`, `category`, `severity`, `requires_entity_resolution`,
`confidence_multiplier`, `enabled`, `trigger_condition` (+ `max_contribution`,
`event_type`).

`trigger_condition` is a small **declarative** spec (`signal_type` /
`min_confidence` / `metadata_equals`) -- deliberately **not** an expression
language. A config file that can execute arbitrary logic is an injection
vector, and this project's standing rule is that data is never instructions.
Anything richer belongs in a signal collector, which is code and gets reviewed.

Validation is fail-fast (duplicate ids, non-binding caps, unordered bands all
raise at load), mirroring ADR-011: a compliance system that quietly
mis-weights evidence is worse than one that refuses to start.

**Weights are expert-set, not calibrated -- and the file says so.** Phase 0 §14
established this dataset cannot support calibration. Presenting them as fitted
would be a lie.

---

## 3. The risk engine

```
weight_x_confidence:  raw = weight × signal.confidence × confidence_multiplier
weight_only:          raw = weight × confidence_multiplier

contribution = min(raw, factor.max_contribution)
score        = min(SUM(contributions), scoring.max_total_score)
```

Reproduces the brief's worked example exactly: **weight 50 × confidence 0.82 →
contribution 41** (`test_matches_the_brief_worked_example`). The formula name
itself comes from config -- switching to `weight_only` is a config edit, tested.

**Additive and capped, not normalized.** Unlike the Phase 3 resolution engine
(which divides by applicable weight, because it answers *"how well do these two
records agree?"*), risk answers *"how much risk has accumulated?"* A client with
one confirmed sanctions hit and no other data must not score higher than the
same client with additional benign attributes -- which is exactly what
normalizing would do.

**Highest contribution per factor wins.** Two adverse-media articles are not
twice the risk of one; they are one finding with corroboration. Repetition is
surfaced by the *alert* engine (`REPEATED_SIGNAL`), never by inflating the
score.

**Unmatched signals are reported, not dropped** (`unmatched_signals`) -- a
registry gap should be visible.

### Bands

From config: LOW ≥ 0, MEDIUM ≥ 25, HIGH ≥ 50, CRITICAL ≥ 80.

### Explainability

Every contribution shows its arithmetic:

> `Upstream sanctions flag: 40 × 1.00 confidence = 40.0 pts. Client master
> carries an upstream sanctions label. Not independently verified by this system.`

And every score explains itself:

> `Risk 53/100 -> HIGH. Driven by: Upstream sanctions flag +40, High-risk sector
> +8, Ownership opacity +5.`

Contributions are persisted as JSON on the snapshot, so a score explains itself
later **without re-running the engine** against data that may since have changed.

---

## 4. Risk events (immutable)

Every monitored signal that matches a factor becomes a `RiskEvent` carrying:
id, type, timestamp, severity, confidence, evidence, entity ref, client,
source, trigger, status, and the `factor_id` that classified it.

**Immutability is structural**: `RiskEventRepository` has no `update` or
`upsert` method (asserted by test). An observation that was true when observed
stays on the record.

### Change detection

`dedup_key` fingerprints the **finding**, never the observation. It contains no
timestamp and no run id -- otherwise every cycle would invent "new" findings
and change detection would be meaningless. Uniqueness is enforced by a DB
constraint `(client_id, dedup_key)`, not just a pre-check, so a race can't
create a duplicate.

Two deliberate key choices worth noting:
- Transaction typologies key on the **flagged ratio**, not the raw count, so a
  client whose profile is materially unchanged doesn't generate a "new finding"
  every time one more transaction lands.
- Provider failures key on **provider + status**, not the error text, so a
  flapping provider whose message varies doesn't spawn endless events.

---

## 5. Alerts

| Trigger | Fires when |
|---|---|
| `BAND_ESCALATION` | Band moved **up** into a configured escalation band |
| `SCORE_DELTA` | Score rose ≥ `min_score_delta` |
| `CRITICAL_EVENT` | A new event of a configured critical type appeared |
| `REPEATED_SIGNAL` | ≥ N new events of the same type in one cycle |
| `PROVIDER_DEGRADED` | A provider failed → coverage incomplete |

Each alert carries severity, reason, trigger, risk delta, and linked
events/evidence. Duplicate suppression is a DB unique constraint on
`(client_id, dedup_key)`.

**A real false-alert defect was found in live testing and fixed.** The first
run produced *"2 new OTHER findings in one monitoring cycle"* — `high_risk_sector`
and `ownership_opacity` both map to the `OTHER` catch-all event type, so two
**entirely unrelated facts** were reported as repetition. Repetition means *the
same kind of finding recurred*, and a catch-all bucket isn't a kind. Fixed via
`repeated_signal_excluded_event_types` in config (not hardcoded, so it stays
policy), with a regression test both ways. See ADR-020.

---

## 6. Provider orchestration

Reuses Phase 2's `ProviderExecutionService` wholesale -- timeouts, retries, and
`shutdown(wait=False)` non-blocking cancellation (ADR-008) all still apply. The
monitoring layer adds:

- **Parallel execution** across provider categories (`ThreadPoolExecutor`).
- **Failure as data, never as an exception.** A provider that raises, times
  out, or rate-limits becomes a `PROVIDER_FAILURE` signal.
- **Weight 0 for failures.** An outage must be *visible* but must **never**
  raise a client's risk. Verified by test.
- **`NOT_CONFIGURED` is not a failure** -- it's a provider that was never
  expected to answer. Treating it as degraded coverage would cry wolf on every
  cycle in a default install.
- **One client's failure never stops a sweep** -- `monitor_many` isolates
  per-client exceptions into a failed cycle result.

---

## 7. APIs

| Method & path | Purpose |
|---|---|
| `POST /api/v1/monitor/client/{id}` | One monitoring cycle |
| `POST /api/v1/monitor/all` | Sweep: paginated, selected ids, or high-risk only |
| `GET /api/v1/risk/{client_id}` | Latest assessment |
| `GET /api/v1/risk/history/{client_id}` | Append-only score history |
| `GET /api/v1/risk/factors` | The live registry — the scoring model, inspectable |
| `GET /api/v1/events/{client_id}` | Risk events |
| `GET /api/v1/alerts` | Filter by client/status/severity |
| `GET /api/v1/alerts/{id}` | Alert + linked events/evidence |

`GET /risk/{id}` on a never-monitored client returns `never_monitored: true`
with a **null** score — deliberately not `0/LOW`, which would assert "we
assessed them and they're fine" when we never looked.

Alerts are **read-only**: acting on one is a human-review decision reserved for
a later phase. Asserted by test.

---

## 8. Real-dataset regression

Client 3 ("Phillips-Hanson"), whose attributes Phase 0 measured exactly
(sanctions_flag=1, UAE, NGO/Charity High sector, opacity 0.5):

```
score = 40 (upstream sanctions flag)
      +  8 (high-risk sector)
      +  5 (ownership opacity: 10 × 0.5)
      = 53.0 -> HIGH
```

Pinned in `test_real_client_monitoring_cycle_produces_deterministic_score`.

The upstream-flag event's summary explicitly says *"Not independently verified
by this system"* — Phase 0 §3 measured 0/2000 client names matching the
authoritative lists, so this system did **not** derive that flag and must never
imply it did. Asserted by test.

---

## 9. Known limitations

1. **Weights are expert-set, not calibrated** (§2). Unavoidable given the data.
2. **Monitoring is synchronous** — no task queue exists (Celery/Redis are out
   of scope, Phase 0 §11). `/monitor/all` is capped and paginated; a full
   2,000-client sweep is many requests, not one.
3. **Parallelism is across provider *categories*, not clients.** Only one
   category (adverse media) exists today, so the thread pool is currently just
   the orchestration seam. Per-client parallelism would fight single-writer
   SQLite (ADR-001).
4. **`monitor_high_risk` falls back to profile-flagged clients** when a client
   has never been scored — otherwise a fresh install's "high risk" set would be
   empty and monitor nothing.
5. **Most clients cannot exceed the profile-driven factors.** Phase 0's finding
   persists: client names don't resolve to authoritative sanctions data, so a
   real client's score is driven by upstream labels + structural attributes, not
   by matches this system found. Reproduced honestly, not hidden.
6. **Transaction typology signals use pre-computed flags** from the shallow
   dataset. Deep SAML-D behavioural scoring is not wired into monitoring (the
   provider exists but a ~18s/account scan on every cycle is not viable).
7. **No transitive/entity-level risk propagation** — a client's score doesn't
   inherit from a resolved UBO's risk. Ownership exposure is a factor, but the
   graph isn't traversed for scoring.

---

## 10. Boundary: what Phase 4 did NOT build

No LLM investigation, no timeline, no SAR, no frontend, no human-review UI.
`Investigation`/`InvestigationFinding`/`SARDraft`/`HumanReview` remain
schema-only, exactly as Phase 1 left them.

Phase 4 produces **scores, events, and alerts**. Investigating them, narrating
them, and deciding on them belong to later phases — and the deciding must stay
with a human.
