# Architecture Decision Records

**Continuous KYC Autonomous Auditor**

This log records every major design choice made across all phases, why it
was made, and what it costs. Entries are numbered and never renumbered or
deleted -- a superseded decision gets a new ADR that says so, the old one
stays for the record.

**Maintenance rule: every future phase adds its own ADR entries here before
being marked complete.** This file is not a Phase 2 artifact -- it is a
permanent project log, starting from Phase 0 and continuing to the end of
the project.

Each entry: **Context** (what problem existed), **Decision** (what was
chosen), **Consequences** (what that costs or enables), and **Status**.

---

## ADR-001: SQLite over PostgreSQL

**Phase:** 0/1 **Status:** Accepted

**Context:** The system needs a durable relational store for client,
transaction, sanctions, and workflow data at hackathon scale.

**Decision:** Use SQLite via SQLAlchemy, not PostgreSQL. No `pgvector`,
no separate DB server process.

**Consequences:** Zero deployment/ops overhead, trivial local setup, single
file backup. Costs: single-writer concurrency (fine -- no concurrent-write
requirement exists or is anticipated at this scale), and SQLite's more
primitive query planner without `ANALYZE` surfaced a real bug (see
ADR-006) that a cost-based optimizer like Postgres's would likely have
avoided. Revisit only if a genuine multi-writer concurrent-access
requirement emerges -- nothing in the current data or requirements suggests
one will.

---

## ADR-002: Two-tier sanctions provenance, never merged

**Phase:** 0/1 **Status:** Accepted

**Context:** The dataset contains both real, full-scale OFAC/OpenSanctions
data (Tier 1) and a small, deliberately-curated demo fixture (Tier 2,
interlocked with the UBO/adverse-media narrative -- see
`docs/phase-0-dataset-audit.md` SS4.5). Treating these as one undifferentiated
"sanctions data" pool would let a demo-only match be mistaken for a real
regulatory hit.

**Decision:** `SourceTier` (`TIER_1_AUTHORITATIVE` / `TIER_2_CURATED_DEMO` /
`INTERNAL` / `EXTERNAL_LIVE`) is a non-nullable column on every
provenance-bearing row and every provider result. No query, repository
method, or API response merges tiers without the tag traveling with the
record.

**Consequences:** Every sanctions-adjacent read (repository query, provider
result, API response) must carry and expose this field -- slightly more
verbose schemas and DTOs, in exchange for structural impossibility of the
single most reputationally dangerous mistake this system could make (citing
a fictional demo entity as a real hit).

---

## ADR-003: Canonical file paths only; duplicates never registered

**Phase:** 0 **Status:** Accepted

**Context:** `clients_with_fatf_ofac.csv` and `transactions_with_fatf_ofac.csv`
exist as byte-identical duplicates at both `data/` root and
`data/kyc_profiles/` (verified by MD5).

**Decision:** `app/registry/sources.py` registers only the `kyc_profiles/`
copies. The root-level duplicates are never referenced by any code path.

**Consequences:** No risk of a future ingestion job double-counting records
by globbing both locations. The duplicate files themselves are left on disk
untouched (not our call to delete Phase 0 data).

---

## ADR-004: Provider/adapter architecture, not dataset-specific code

**Phase:** 1 **Status:** Accepted

**Context:** An explicit architecture directive required the system to never
be hardcoded around the Phase 0 dataset's specific clients or demo entities
-- the same pipeline must work for any entity, and the design must support
future live external APIs (sanctions, adverse media, corporate registry)
without rework.

**Decision:** Five `typing.Protocol` contracts (`SanctionsProvider`,
`AdverseMediaProvider`, `CorporateRegistryProvider`, `TransactionProvider`,
`OwnershipProvider`), normalized response schemas
(`ExternalEntityCandidate`, `ExternalArticle`, generic `ProviderResult[T]`
with a graceful-degradation status enum), and a `ProviderRegistry` that
resolves providers by category at runtime. Every provider method takes a
plain string/ID -- no method signature can reference a specific entity.

**Consequences:** A future live API integration is exactly one new class
satisfying an existing Protocol plus one `registry.register(...)` call --
proven in Phase 2, which added four new providers (two Tier-1 sanctions
lookups, one SAML-D transaction lookup, one local adverse-media search)
without touching the Protocol definitions or any existing provider. Cost:
an extra layer of indirection (Protocol + normalized schema) that a
single-dataset-only design wouldn't need.

---

## ADR-005: Internal surrogate IDs, decoupled from source IDs

**Phase:** 1 **Status:** Accepted

**Context:** Source data (e.g. `clients_with_fatf_ofac.csv`'s `client_id`)
should not silently become the application's only identity concept -- a
future re-ingestion, a schema evolution, or a second data source with an
overlapping ID space could collide.

**Decision:** Every ingested entity has an autoincrement internal `id` (the
real primary/foreign key throughout the schema) plus a separately-preserved
`external_*_id` (unique, but not the PK). `SanctionsEntity` additionally
scopes external-ID uniqueness to `(source_type, external_entity_id)` since
OFAC and OpenSanctions ID spaces are independent.

**Consequences:** Every FK join goes through the internal ID (one extra
lookup during ingestion, e.g. `ClientRepository.map_external_to_internal_ids()`
for bulk resolution). In exchange, the API can expose the human-meaningful
external ID in URLs (`GET /customers/{external_client_id}`) while the schema
stays free to ingest a second, overlapping-ID-space source later without a
migration.

---

## ADR-006: Composite index required to avoid SQLite query-planner misselection

**Phase:** 2 **Status:** Accepted (bug fix)

**Context:** Ingesting the 50,000-row shallow transaction file went from a
few seconds (small chunks) to an unbounded hang -- 285+ seconds and still
climbing when killed. Root-caused via a sequence of isolated benchmarks
(raw `sqlite3` vs. ORM, pure INSERT vs. SELECT-then-INSERT, growing vs.
fixed-size table) to `EXPLAIN QUERY PLAN`: the upsert lookup's WHERE clause
(`transaction_source = ? AND external_transaction_id = ?`) was being
answered via `SEARCH transactions USING INDEX ix_transactions_transaction_source`
-- SQLite chose the **low-cardinality** single-column index (2 distinct
values) over the high-selectivity one, because SQLite's planner has no
`ANALYZE` statistics by default and picks a plan heuristically among several
candidate indexes on a multi-table-index schema. This turned every upsert
lookup into an effective near-full-table scan, cost growing linearly with
table size.

**Decision:** Replace the two competing single-column indexes with one
composite `UNIQUE(transaction_source, external_transaction_id)` constraint
-- which is also the exact natural key documented in `app/ingestion/base.py`.
Removed the now-redundant standalone index on `transaction_source`.
Confirmed via `EXPLAIN QUERY PLAN` that the composite index is selected
correctly, and empirically re-measured: full 50,000-row ingestion dropped to
**41 seconds** (idempotent re-run: 39 seconds, confirming no regression on
the update path).

**Consequences:** Any future high-volume table upserted by a natural key
**must** get a composite index/unique-constraint matching that exact key --
single-column indexes on the individual key parts are not just insufficient,
they can actively mislead SQLite's planner. `ingest_all()` also now runs
`ANALYZE` after a full load (cheap, defense-in-depth against the same class
of bug recurring as new indexes are added later).

---

## ADR-007: Explicit `cast(..., Integer)` required around boolean aggregate SUMs

**Phase:** 2 **Status:** Accepted (bug fix)

**Context:** `TransactionRepository.summary_for_client` computed
`flagged_count` as `func.sum(<OR of 5 boolean-comparison columns>)`. Against
real data (client_id=3, 22 of 25 transactions genuinely flagged, verified in
Phase 0), this returned Python `True` instead of `22`. SQLAlchemy infers a
boolean-comparison expression's SQL result type as `Boolean`; its Boolean
result-value processor coerces *any* non-zero integer returned by SQLite
back into Python `True` when the query result is consumed through that
type -- silently discarding the real count.

**Decision:** Wrap the OR'd condition in `cast(..., Integer)` before
summing: `func.sum(cast(or_(...), Integer))`. Verified directly (raw
`SELECT`, then the fixed ORM query) that this returns `22`, not `True`.

**Consequences:** Every future boolean-aggregate query in this codebase
must use the same `cast(..., Integer)` pattern -- flagged in the code
comment at the fix site specifically so this isn't rediscovered from
scratch. This is a general SQLAlchemy pitfall, not specific to this schema;
worth checking for in any future `func.sum()`/`func.count()` over a boolean
expression.

---

## ADR-008: `ThreadPoolExecutor` must be shut down with `wait=False` on timeout

**Phase:** 2 **Status:** Accepted (bug fix)

**Context:** `ProviderExecutionService`'s timeout handling used
`with ThreadPoolExecutor(max_workers=1) as executor:`. On a genuine timeout
(`future.result(timeout=...)` raising), the code correctly returned a
`TIMEOUT` `ProviderResult` -- but the `with` block's `__exit__` calls
`executor.shutdown(wait=True)` by default, which **blocks until the
already-submitted (hung) task actually finishes**. A test against a
deliberately 5-second-hanging provider with a 0.3s timeout and 3 retry
attempts took 15 seconds wall-clock, not ~1 second -- the "timeout" label
was correct but the caller was never actually freed sooner than the real
hang duration.

**Decision:** Manage the executor without a context manager; call
`executor.shutdown(wait=False)` explicitly in the timeout/error paths, so
the calling thread returns the moment the deadline passes. The abandoned
worker thread is left to finish (or die) in the background -- Python cannot
forcibly kill a running thread, which is a fundamental, well-known
limitation of timing out synchronous code, not something a different API
could avoid.

**Consequences:** A genuinely hung future external-API call will no longer
block a request past its configured timeout, at the cost of a lingering
background thread until that call's own underlying I/O eventually gives up
(e.g. the OS socket timeout). Verified: the same test now returns in 0.66s
instead of 15s.

---

## ADR-009: Customer 360's live provider lookups are opt-in, not default

**Phase:** 2 **Status:** Accepted

**Context:** Querying every registered sanctions provider (including the
Tier-1 OpenSanctions stream over a 488 MB file, measured at 40-45s) or the
SAML-D transaction provider (measured up to ~18s for an unbounded scan) on
every `GET /customers/{id}/360` call would make the endpoint unacceptably
slow for routine use, violating the standing "application must remain fast"
principle from Phase 1.

**Decision:** `include_sanctions_lookup`, `include_adverse_media_lookup`,
and `include_deep_transactions` are independent query parameters, all
defaulting to `False`. The fast path (client + accounts + shallow-transaction
summary, all from SQLite) responds in milliseconds; a caller opts into the
slower live lookups explicitly and accepts the latency.

**Consequences:** A caller who wants the fuller picture must know to ask for
it -- the API is not "batteries included" by default. In exchange, the
common case (browsing the customer list, checking a known profile) stays
fast, and `provider_availability` in the response always reports exactly
which providers were consulted and their outcome, so nothing is silently
partial without being visible as such.

---

## ADR-010: SQLite WAL mode + `synchronous=NORMAL`

**Phase:** 2 **Status:** Accepted

**Context:** SQLite's default rollback-journal mode with `synchronous=FULL`
fsyncs on every commit. Combined with chunked-commit bulk loading (see
ADR-006's investigation), this was a contributing factor to slow bulk
ingestion before the real root cause (ADR-006) was identified.

**Decision:** Set `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`
on every SQLite connection (`app/core/database.py`'s existing
per-connection PRAGMA event listener, extended -- the same mechanism already
used for `foreign_keys=ON`).

**Consequences:** WAL mode batches writes into a write-ahead log instead of
fsyncing every commit; `synchronous=NORMAL` is the documented-safe pairing
with WAL (durable against application crashes; only an extremely narrow
OS-crash-at-exactly-the-wrong-instant window is weaker than `FULL`). Correct
and appropriate tradeoff for a single-writer, hackathon-scale database.
Note: this alone did **not** fix the ADR-006 hang -- that required the
composite index. Keeping both changes, since WAL is a good default
independent of that specific bug.

---

## ADR-011: Entity-resolution weights are configuration, not code

**Phase:** 3 **Status:** Accepted

**Context:** The confidence engine needs ~20 numbers (per-scorer positive
weights, per-scorer conflict penalties, status thresholds). Hardcoding them
in Python would mean a code change + redeploy to retune a compliance
posture, and would hide the single most policy-laden part of the system
inside implementation detail.

**Decision:** Weights live in `backend/config/resolution_weights.json`,
loaded and strictly validated by `app/resolution/config.py`. Validation is
fail-fast: a missing scorer key or a negative weight raises at load time.
There is **no fallback to in-code defaults** -- a fallback would silently
defeat the externalization the moment the file went missing.

**Consequences:** Retuning is a config edit. The file is the honest home for
the caveat that these are *expert-set, not statistically calibrated* values
(Phase 0 SS14 established this dataset can't support calibration). Cost: the
app now has a required config file, and a deployment that loses it fails
loudly at startup rather than scoring everything subtly wrong. That is the
intended trade -- a compliance system that quietly mis-weights evidence is
worse than one that refuses to start.

---

## ADR-012: Scorers return a three-state result, never a boolean

**Phase:** 3 **Status:** Accepted

**Context:** The obvious matching design is "does attribute X match? true/
false". That collapses two genuinely different situations: *"the DOBs
contradict each other"* and *"one side has no DOB at all"*. Most Tier-1 OFAC
rows have empty `Remarks` and therefore no DOB (docs/data-dictionary.md), so
treating absent-as-mismatch would reject nearly every true match in this
dataset.

**Decision:** Every scorer returns a `ScorerResult` with a score, a
human-readable reason, a signed confidence impact, and an explicit
`applicable` flag distinct from `is_conflict`. Three states:
agreement / contradiction / absent. A non-applicable scorer is skipped from
the confidence denominator entirely -- it costs nothing and earns nothing.

**Consequences:** "Absence of evidence is not evidence of absence" is
structural rather than a convention (Phase 0 SS6G called for exactly this
before implementation). The confidence formula must divide by the sum of
*applicable* weights rather than a fixed total, which is slightly less
obvious arithmetic but the only version that behaves correctly on sparse
real data. Also gives explainability its raw material for free.

---

## ADR-013: A conflict is penalized twice, deliberately

**Phase:** 3 **Status:** Accepted

**Context:** Phase 0 SS6 identified the real false positives in this data:
`AL-RASHID TRUST` (a trust, not a person) and `AL-RASHIDI, NAWAF AHMAD
ALWAN` (a different person) both score highly on *name* against a sought
"Mohammad Al-Rashid". If a contradiction merely withheld positive credit,
a strong name match would still carry them to a plausible-looking confidence.

**Decision:** A conflicting scorer both (a) earns ~0 of its positive weight
and (b) subtracts a configured penalty. Penalties intentionally exceed most
single positive weights, so a confirmed entity-type or DOB contradiction can
sink an otherwise-perfect name match. A separate `name_floor` rejects any
pair whose name similarity is below threshold regardless of other agreement.

**Consequences:** Measured outcome: `AL-RASHID TRUST` -> 29.4 AUTO_REJECTED,
`AL-RASHIDI, NAWAF` -> 29.8 AUTO_REJECTED, true hit -> 89.2 HIGH_CONFIDENCE.
This is a precision-over-recall posture appropriate to compliance, and it is
a *policy* choice, which is why the penalties live in config (ADR-011) rather
than in the formula. A genuine match whose sources disagree on one attribute
will be scored down -- accepted, because a human reviewing a downgraded true
match is far cheaper than a wrongly-cleared entity.

---

## ADR-014: `EntityMatch.candidate_sanctions_entity_id` is nullable

**Phase:** 3 **Status:** Accepted (amends a Phase 1 assumption)

**Context:** Phase 1 modelled a match candidate as a non-null FK into our own
`sanctions_entities` table. Phase 2 then made that assumption false: the
Tier-1 OFAC/OpenSanctions providers *stream* their files and deliberately
never persist a row (docs/phase-2-ingestion.md SS3). A provider-sourced
candidate therefore has no local id to point at.

**Decision:** Make the FK nullable. A candidate is identified by whichever is
available: `candidate_sanctions_entity_id` (real FK) for a DB-sourced
candidate, or `candidate_provider` + `candidate_external_id` for a
streaming-provider one. `candidate_name` is always stored so a match is
human-readable without re-querying the source.

**Consequences:** Rejected alternative: bulk-load the Tier-1 files so every
candidate has a row. That would violate the standing "never bulk-load the
large files" rule and inflate SQLite from 18 MB to ~GB purely to satisfy a
constraint. The cost is that the database cannot enforce candidate
referential integrity for provider-sourced matches -- accepted, and
consistent with the polymorphic-association tradeoff already documented for
`EntityMatch.subject_id` in Phase 1.

---

## ADR-015: The resolution pipeline never writes

**Phase:** 3 **Status:** Accepted

**Context:** The engine needs to be usable for (a) scoring two arbitrary
entities with no database at all, (b) what-if scoring without committing,
and (c) real persisted resolution runs.

**Decision:** `app/resolution/pipeline.py` is pure computation and returns a
result. Persisting `EntityMatch` + `Evidence` and writing the audit entry is
`app/services/entity_resolution_service.py`'s job. `POST /entity-resolution/
resolve-pair` exercises the pure path; `resolve` with `persist: false` uses it
too.

**Consequences:** The matching logic is testable with zero fixtures, which is
why the false-positive regression tests need no database. Cost: a caller who
wants persistence must go through the service rather than the pipeline --
a discoverability trade paid back by making "score without side effects" a
first-class, obviously-safe operation.

---

## ADR-016: The engine can never produce CONFIRMED

**Phase:** 3 **Status:** Accepted

**Context:** The project's core principle reserves consequential compliance
decisions for humans. "Confirmed sanctions match" is exactly such a decision.

**Decision:** `EntityMatchStatus` gained POSSIBLE and HIGH_CONFIDENCE
(additively -- no existing value removed or renamed). The confidence engine's
`status_for()` can only return CANDIDATE / POSSIBLE / HIGH_CONFIDENCE /
AUTO_REJECTED. CONFIRMED and HUMAN_REVIEWED are unreachable by any automated
path, enforced by (a) an exhaustive test over the reachable status space and
(b) a runtime `_assert_machine_status` guard that raises rather than persists.
`AUTO_REJECTED` serves as the brief's "Rejected" state under its existing
Phase 1 name, rather than adding a near-duplicate.

**Consequences:** HIGH_CONFIDENCE explicitly does not mean "confirmed" -- it
means "strong enough that a human should look". The distinction is enforced
in code, not documentation, so a later phase cannot quietly erode it.

---

## ADR-017: Risk scoring is additive-and-capped, not normalized

**Phase:** 4 **Status:** Accepted

**Context:** Phase 3's entity-resolution engine divides earned weight by
*applicable* weight, because it answers "how well do these two records
agree?" -- a pair lacking DOB data shouldn't be punished for it. The obvious
move was to reuse that shape for risk. It is wrong here.

**Decision:** Risk contributions are summed and capped
(`score = min(SUM(contributions), max_total_score)`), never normalized by
available factors.

**Consequences:** Risk answers a different question: "how much risk has
accumulated?" Under normalization, a client with one confirmed sanctions hit
and no other data would score *higher* than the same client with additional
benign attributes -- because the denominator would grow. That is absurd for
compliance. The cost is that the score is only interpretable relative to the
configured weights, not as a percentage of "risk possible"; the band
thresholds in config carry that interpretation instead. Two engines, two
formulas, deliberately -- documented because the asymmetry looks like an
inconsistency until you know why.

---

## ADR-018: Highest contribution per factor wins; repetition is an alert, not a multiplier

**Phase:** 4 **Status:** Accepted

**Context:** When several signals match one factor (e.g. three adverse-media
articles), the engine could sum them, average them, or take the best.

**Decision:** Take the highest single contribution per factor. Repetition is
surfaced by the alert engine's `REPEATED_SIGNAL` trigger instead.

**Consequences:** Three articles about one allegation are corroboration of a
single finding, not three times the risk -- summing would let a noisy news
cycle drive a client to CRITICAL on one underlying fact. The information is
not lost: it moves to the alert layer, where "3 new adverse-media findings in
one cycle" is exactly the right thing for a human to see. Cost: a client with
genuinely many *distinct* problems in one category scores the same as one with
a single severe one in that category; cross-category accumulation is what
raises the score.

---

## ADR-019: Scoring is stateless over all signals; only events are change-driven

**Phase:** 4 **Status:** Accepted

**Context:** "Only NEW findings create events" (brief SS9) is unambiguous for
events. It is dangerously ambiguous for *scoring*: if the engine only scored
new signals, a client's score would collapse to ~0 on the second cycle when
nothing changed.

**Decision:** Every cycle collects the FULL current signal set and scores all
of it. Change detection governs event and alert creation only.

**Consequences:** A client's score always reflects everything currently true
about them, and re-running a cycle on unchanged data is a no-op that yields an
identical score (tested). Cost: every cycle re-collects every signal, including
re-running entity resolution -- more work per cycle than a purely incremental
design. Accepted: correctness of the number a compliance officer acts on
outranks cycle efficiency, and the expensive providers are opt-in anyway.

---

## ADR-020: A catch-all event type must not count toward REPEATED_SIGNAL

**Phase:** 4 **Status:** Accepted (bug fix)

**Context:** The `REPEATED_SIGNAL` alert counts new events grouped by
`event_type`. Live testing of the first working cycle produced a genuinely
wrong alert: *"2 new OTHER findings in one monitoring cycle"*. Two unrelated
factors -- `high_risk_sector` and `ownership_opacity` -- both map to the `OTHER`
catch-all event type, so two entirely different facts were reported to an
analyst as repetition.

**Decision:** Add `repeated_signal_excluded_event_types` to the alert config
(defaulting to `OTHER`, `PROVIDER_FAILURE`, `ENTITY_CONFLICT`). Repetition means
"the same *kind* of finding recurred"; a catch-all bucket is not a kind, a
provider outage repeating is an ops issue not corroboration, and a resolution
conflict repeating is the false-positive machinery working as designed.

**Consequences:** The fix lives in configuration rather than as a hardcoded
`if event_type == OTHER` check, so the policy stays tunable and is covered by
tests in both directions (excluded -> silent; un-excluded -> fires). This is a
good argument for the registry design generally: the bug was a *policy* error,
and it was fixable as a policy edit. Found only by running the thing and
reading the alert text -- no unit test would have flagged a technically-correct
count.

---

## ADR-021: Provider failure is an event with zero weight

**Phase:** 4 **Status:** Accepted

**Context:** When an adverse-media provider times out, the cycle has a hole in
its coverage. Three options: ignore it, fail the cycle, or record it.

**Decision:** Record it as a `PROVIDER_FAILURE` RiskEvent whose factor has
`weight: 0.0`, and raise a LOW-severity `PROVIDER_DEGRADED` alert.
`NOT_CONFIGURED` is explicitly *not* treated as a failure.

**Consequences:** "We looked and found nothing" and "we couldn't look" become
distinguishable in the record -- which matters enormously when a reviewer later
asks why an entity was cleared. Weight 0 is the key discipline: incomplete
coverage is an operational problem and must **never** raise a client's risk
score, or the system would penalize clients for its own outages. Excluding
`NOT_CONFIGURED` prevents crying wolf on every cycle of a default install,
where three external providers are intentionally unconfigured.

---

## ADR-022: Risk factors are declarative config, not an expression language

**Phase:** 4 **Status:** Accepted

**Context:** "Support future expansion without code changes" (brief SS2) invites
a rules DSL -- a config that can express arbitrary conditions.

**Decision:** `trigger_condition` supports exactly three declarative fields:
`signal_type`, `min_confidence`, `metadata_equals`. Anything richer must be
implemented as a signal collector, which is code.

**Consequences:** A config file that can execute arbitrary logic is an
injection vector, and this project's standing rule (docs/phase-1-foundation.md)
is that data is never instructions -- a rule adopted precisely because the
dataset ships with a live prompt-injection payload. The trade is real: some
future factor will want a condition this spec can't express, and it will need a
collector. That is the intended boundary -- collectors get code review, config
does not. Verified expansion still works without code:
`test_new_factor_needs_no_code_change` invents a factor with an unseen
`signal_type` and scores correctly from JSON alone.

---

## ADR-023: An LLM SDK is an optional runtime dependency, imported lazily

**Phase:** 5 **Status:** Accepted

**Context:** Phase 5 is the first phase permitted to call a model, so
`anthropic` enters `requirements.txt` -- a file whose header had said "no LLM
SDKs" since Phase 1. A top-level `import anthropic` in the provider would make
the SDK a hard dependency of the entire application: ingestion, entity
resolution, the deterministic risk engine, and all 302 pre-existing tests would
fail to start on a machine without it.

**Decision:** The dependency is declared, but imported *inside* the methods of
`app/providers/anthropic_llm_provider.py`. `is_configured()` returns False when
the SDK is absent, exactly as it does when the API key is absent, and the
investigation is recorded as FAILED with an actionable reason.

**Consequences:** The one component that needs a model is the only component
that cares whether the model SDK exists. The deterministic core stays runnable
and testable in an environment with no LLM tooling at all -- not a hypothetical,
but how this project's whole test suite runs today. The cost is that an import
typo surfaces at call time rather than at startup; accepted, because
`is_configured()` is exercised by tests and the failure is a recorded status
rather than a crash.

---

## ADR-024: The agent depends on a Protocol, never on a vendor

**Phase:** 5 **Status:** Accepted

**Context:** The brief requires that "Claude/OpenAI should be interchangeable"
and that no single model be hardcoded. The obvious reading -- ship an Anthropic
client *and* an OpenAI client -- collides with two standing rules: never write
fake implementations that pretend to call APIs, and never add infrastructure
merely to make the architecture look complex.

**Decision:** Define `LLMProvider` as a runtime-checkable Protocol
(`app/providers/llm_contracts.py`), mirroring the Phase 1 data-provider
contracts. The agent, orchestrator, prompts, grounding validator, API, and
persistence layer import no vendor SDK and name no vendor.
`app/providers/llm_registry.py` resolves a configured name to an
implementation. Exactly one implementation ships: Anthropic, which is real.

**Consequences:** Swapping vendors is a new class, one registry line, and one
`.env` value. The claim is demonstrated rather than asserted:
`tests/fake_llm.py` provides a provider with no Anthropic involvement, and the
entire pipeline -- including the API tests -- runs on it unchanged. Shipping an
untested OpenAI client would have been precisely the "fake integration" the
rules ban; the second implementation belongs to whoever holds that key and can
test against it. The contract is narrowed to one method, `complete_json`,
because every use in this project needs schema-constrained output -- exposing
free-text chat would invite an unvalidated string into a compliance record.

---

## ADR-025: No sampling parameters are sent, and temperature is recorded as null

**Phase:** 5 **Status:** Accepted

**Context:** The brief's evaluation metadata (SS10) lists Temperature.
Current-generation Anthropic models **reject** `temperature`, `top_p`, and
`top_k` with HTTP 400 rather than defaulting them -- sending one is a hard
failure, not a no-op.

**Decision:** No sampling parameter is sent. `temperature` exists on
`LLMInvocationResult`, on the `investigations` table, and in the evaluation API
response, and is `null`.

**Consequences:** The metadata field the brief asks for is present and honest.
Writing `0.0` would have satisfied the requirement cosmetically while
fabricating a request parameter that was never transmitted -- a small lie of
exactly the kind this project has refused since Phase 0, where
`laundering_labelled_count` is None rather than 0 when the source carries no
label. The column is kept rather than dropped because a future provider may
legitimately use one. Enforced by
`test_anthropic_request_never_sends_a_sampling_parameter`, which inspects the
AST of the real call rather than grepping the source -- the first version of
that test grepped, and passed by matching its own docstring.

---

## ADR-026: Chain-of-thought is never stored, because it is never requested

**Phase:** 5 **Status:** Accepted

**Context:** The brief says: never store chain-of-thought. The obvious
implementation is to receive reasoning and drop it before persistence.

**Decision:** `thinking.display` is pinned to `"omitted"` on every request.
Adaptive thinking stays **on** -- grounding a claim in specific evidence ids is
exactly the kind of checking that benefits from it -- but no reasoning is ever
returned. There is no column for it and nothing to put in one.

**Consequences:** "We do not store X" becomes "we never receive X", which no
future maintainer can accidentally undo by adding a logging line. `"omitted"` is
already the default on current models; setting it explicitly makes the intent
legible in the code and means a future default flip cannot silently start
returning reasoning. The report's `reasoning` field is unaffected and is *not*
CoT: it is an authored, reader-facing rationale, in the same sense that a human
analyst's written justification is not a transcript of their thoughts. Enforced
by `test_anthropic_request_never_asks_for_reasoning`.

---

## ADR-027: The recommendation vocabulary is a closed enum with no APPROVE/REJECT

**Phase:** 5 **Status:** Accepted

**Context:** The brief permits six recommendations and states the agent must
never recommend final approval or rejection. A prompt instruction alone is a
request, and this project's core design principle exists precisely because a
model's cooperation is not an architectural guarantee.

**Decision:** `InvestigationRecommendationAction` contains the six permitted
actions and nothing else. It is emitted into the model's JSON Schema as an
`enum`, typed on the Pydantic layer, typed on the database column, and
re-checked deterministically in `grounding.py`.

**Consequences:** A recommendation to approve or reject a client is
unrepresentable at every layer -- constrained decoding cannot generate it,
Pydantic rejects it, the column will not store it, and the validator flags it.
Four gates is not redundancy theatre: the schema could be relaxed by a future
edit, and the enum re-check costs one set lookup to guard the single thing this
phase must never do.
`test_an_illegal_recommendation_is_rejected_even_if_the_schema_is_bypassed`
simulates a provider whose constrained output failed and proves APPROVE still
cannot reach the database.

---

## ADR-028: Grounding is deterministic post-validation; failures are flagged, not deleted

**Phase:** 5 **Status:** Accepted

**Context:** Hallucinated citations are the central risk of putting a model
anywhere near a compliance file. Two questions follow: how is one detected, and
what happens to it?

**Decision:** Detection is deterministic code
(`app/investigation/grounding.py`), never a second model. It is possible at all
because the agent is contained: it has no tools and no database session, so
`context.allowed_evidence_ids` is provably the complete set of ids it could
legitimately know, and anything else was invented. A report citing a
nonexistent id is persisted **verbatim**, with `grounding_passed=False`, a
`hallucinated_citation_count`, and each offending finding marked `UNGROUNDED`.
A fabricated id never occupies the `evidence_id` foreign key.

**Consequences:** The component judging the LLM is not itself an LLM, so the
check is as reliable as ordinary code and as testable. Flagging rather than
deleting is the load-bearing half: silently dropping a bad finding would erase
the single most important signal a reviewer could receive -- that this model
hallucinated on this client's file -- and would make the run look cleaner than
it actually was. An *uncited* finding is deliberately **not** a hard failure:
statements sourced from coverage rather than evidence ("no adverse-media
provider was configured") are true and useful, and failing them would pressure
the model to attach an unrelated id purely to satisfy the validator --
manufacturing the exact problem the validator exists to catch.

---

## ADR-029: A successful investigation always terminates at AWAITING_HUMAN_REVIEW

**Phase:** 5 **Status:** Accepted

**Context:** The agent may recommend ESCALATE or CLOSE_INVESTIGATION. Letting a
recommendation drive the status would be a natural-looking automation, and a
quiet inversion of the project's core principle.

**Decision:** Every successful investigation ends at `AWAITING_HUMAN_REVIEW`,
whatever the agent recommended. `ESCALATED` and `CLOSED` are unreachable from
every automated path: `InvestigationRepository` exposes no method that could set
them, and the API exposes no endpoint to close, accept, or decide. A run that
produced no report ends at the new terminal state `FAILED`.

**Consequences:** "Humans make the final compliance decision" is enforced by the
absence of a code path rather than by a service politely declining to call one --
the same technique that keeps `CONFIRMED` out of the resolution engine (ADR-016)
and `update` off the risk-event repository (ADR-003's discipline extended).
`FAILED` is deliberately distinct from `CLOSED`: "we investigated and closed it"
and "we could not investigate" are opposite facts, and collapsing them would let
a coverage gap read as a clean result -- the same reasoning behind ADR-021,
where a provider failure is an event with zero weight.

---

## ADR-030: Groq as a second LLM provider -- the vendor swap that tested ADR-024

**Phase:** 5 (post-phase change) **Status:** Accepted

**Context:** ADR-024 shipped one LLM provider and *claimed* a second vendor
would need only a new class, a registry line, and configuration. A claim like
that is worth nothing until someone cashes it.

**Decision:** Add `GroqLLMProvider` implementing the same `LLMProvider`
Protocol. Groq's own key/model namespace (`GROQ_API_KEY`, `GROQ_MODEL`) rather
than overloading the Anthropic settings, because a model id is vendor-specific
by nature. Anthropic remains fully supported and selectable.

**Consequences:** The claim held. The agent, orchestrator, prompts, grounding
validator, persistence layer, API, and report schema were **unchanged** --
asserted by `test_no_component_outside_the_provider_layer_mentions_groq`, which
greps every module in `app/` and permits the word "groq" in exactly three files.
This is a real test of the seam, because the two vendors differ substantially
and every difference had to be absorbed inside the provider:

| | Anthropic | Groq |
|---|---|---|
| Transport | streaming | **non-streaming** (streaming and structured outputs are mutually exclusive) |
| Schema | `output_config.format` | `response_format.json_schema` (+`strict`) |
| Suppress reasoning | `thinking.display="omitted"` | `include_reasoning=False` |
| Sampling params | **rejected** (HTTP 400) | accepted |
| Usage fields | `input_tokens`/`output_tokens` | `prompt_tokens`/`completion_tokens` |

Three findings worth recording:

1. **ADR-025 was vindicated by its own hypothetical.** It kept the `temperature`
   column despite Anthropic rejecting sampling parameters, reasoning that "a
   future provider may legitimately use one". Groq is that provider; the field
   is now non-null on live runs (`temperature=0.0`), and the evaluation API
   reports the value actually sent.

2. **ADR-026 nearly broke silently on the vendor swap.** The obvious lever,
   `reasoning_format="hidden"`, is **not supported** on the gpt-oss models --
   which are the only ones offering strict structured output -- and those models
   return reasoning in a `reasoning` field **by default**. The default
   configuration would therefore have received chain-of-thought on every call.
   The correct lever is `include_reasoning=False`, with a test asserting the
   provider never reads `.reasoning` at all. "Never receive it" is a per-vendor
   mechanism, not a portable flag.

3. **The report schema was already strict-mode compatible.** Groq's strict mode
   requires every property `required` and `additionalProperties: false`
   throughout -- which Phase 5's schema did for its own reasons. A regression
   test now pins it, so a future schema edit cannot break the Groq path while
   leaving Anthropic green.

**Operational note (paid for in a real 413):** Groq's tokens-per-minute
accounting **reserves `max_completion_tokens` up front**. A ~2.8k-token prompt
with `max_completion_tokens=8000` bills as ~10.8k and is rejected on an 8k-TPM
tier, even though nothing about the prompt is oversized. `LLM_MAX_OUTPUT_TOKENS`
is therefore a TPM setting, not merely an output cap. Groq returns this as HTTP
413 carrying `"code": "rate_limit_exceeded"`, so it maps to `RATE_LIMITED`, not
`ERROR` -- it is a tier limit, not a malformed request, and the status enum
exists to carry exactly that distinction.

---

## ADR-031: The test suite must be hermetic with respect to credentials

**Phase:** 5 (post-phase change) **Status:** Accepted

**Context:** `Settings` reads `backend/.env`. Phase 5's tests constructed
orchestrators via the default path in places, which resolves a provider from
those settings. With no key configured this was harmless -- and it was how the
suite always ran, so the hazard stayed invisible.

The moment a real `GROQ_API_KEY` landed in `.env`, the suite began making
**live, billed API calls**: runtime went from ~15 seconds to **62 minutes**, and
two tests asserting the "no key configured" path failed -- correctly, because a
key *was* now configured.

**Decision:** `tests/conftest.py` blanks `LLM_API_KEY`, `ANTHROPIC_API_KEY`, and
`GROQ_API_KEY` and pins `LLM_PROVIDER` **before any `app.*` import**, alongside
the existing `DATABASE_URL` redirect. Empty strings rather than deletions: an
env var outranks the `.env` file in pydantic-settings, whereas deleting it would
let the file's value through.

**Consequences:** The suite is hermetic -- no test can reach a live model, and
no result depends on whose machine it runs on. This mirrors the existing
`DATABASE_URL` discipline, which redirects to a temp file so tests never touch
the real database; credentials deserved the same treatment and did not have it.
Tests needing a model inject one via `tests/fake_llm.py`. Tests needing a *real*
call are not tests: they are the live verification in
`docs/phase-5-investigation-agent.md` SS10, run deliberately.

---

## ADR-032: A Case is an anchor, not a copy

**Phase:** 6 **Status:** Accepted

**Context:** The brief asks a case to "aggregate" customer, Customer360,
investigation, evidence, matches, risk history, events, alerts, audit, and SAR.
The convenient implementation copies the useful bits -- current score, latest
summary, evidence count -- onto the `cases` row so the queue and the workspace
read in one query.

**Decision:** `Case` stores ONLY what is genuinely its own: lifecycle state,
assignee, and open/close timestamps and reasons. Everything else is read
through a foreign key at request time by `CaseService`.

**Consequences:** No denormalised field can drift. A workspace showing a stale
score beside a live investigation is worse than no workspace -- a reviewer who
cannot trust one number on the page cannot trust any of them. The cost is real
and accepted: `_summarize` issues per-case queries, so a 10k-case queue would
need denormalised counters. At this scale (single-writer SQLite, ADR-001) that
cost is nothing and the correctness is everything.
`test_case_aggregates_the_client_without_copying_it` asserts the column set
never grows a duplicate, so the shortcut cannot be taken later by accident.

---

## ADR-033: The timeline is generated, and has no append method

**Phase:** 6 **Status:** Accepted

**Context:** "Never manually assemble timelines. Generate from stored events."
(brief SS3). A builder with an `add_entry()` helper would satisfy the letter of
that while leaving the door open.

**Decision:** `TimelineBuilder` exposes exactly one public method, `build`.
Nine collectors project rows from existing tables. There is no API by which a
caller could place an entry on a timeline.

**Consequences:** A timeline you can append to is one someone can append to
*incorrectly*, and in a compliance file a plausible fabricated step is worse
than a gap. Enforced by `test_timeline_has_no_public_append_method`, which
asserts the public surface is `{"build"}` -- so adding a helper breaks a test
rather than passing review.

Three sub-decisions carry their own weight:

* **Dedup on `entry_key = "{type}:{source_id}"`, never on rendered text.** Two
  distinct risk events can legitimately have identical summaries; keying on the
  title would silently delete one from the record. Same discipline as Phase 4's
  `dedup_key` and Phase 5's `context_hash`.
* **Order is `(timestamp, entry_key)`.** A monitoring cycle writes a snapshot
  and several events in the same instant, so ties are routine, not edge cases.
  Without the tiebreaker two reads of one case disagree, and a timeline that
  reshuffles on refresh is one no reviewer will trust.
* **Every entry carries an actor.** SYSTEM observed / AGENT wrote / HUMAN
  decided. Collapsing those would let a model's opinion and a compliance
  officer's decision look alike in the one artefact where that distinction
  matters most.

**Paid for in a real bug:** SQLite returns naive datetimes even from
`DateTime(timezone=True)` columns. Sorting naive against aware raises
`TypeError`, so a timeline mixing them crashes on the first case that has both
-- which is every real case. `_utc()` normalises at the collector boundary.

---

## ADR-034: Validate, then mutate, then record

**Phase:** 6 **Status:** Accepted

**Context:** A review does three things: move the case, write a HumanReview,
write an AuditLog. The order is not cosmetic.

**Decision:** `CaseService.apply_review` resolves and validates the transition
BEFORE touching anything. An illegal action raises `ReviewRejectedError` having
written nothing -- no review, no audit row, no partial state change.

**Consequences:** A review row recorded for a transition that was rejected would
be a lie in the audit trail, and the audit trail is the product. Asserted by
`test_an_illegal_action_writes_nothing_at_all`, which counts reviews and audit
rows across a rejected call.

The API surfaces this as **409, not 400**: the request is well-formed and would
be valid at another time -- it conflicts with the case's *current* state. A 400
would send a caller to debug a correct request. The detail names the actions
that are permitted now, and `available_actions` on the case response means a
caller never has to guess in the first place.

---

## ADR-035: Human review is the only unlock for every reserved state

**Phase:** 6 **Status:** Accepted

**Context:** Three earlier phases deliberately made states unreachable and
documented that a later phase would reach them:

* ADR-016 (Phase 3): the resolution engine may never write `CONFIRMED` /
  `HUMAN_REVIEWED`, guarded at runtime.
* ADR-029 (Phase 5): an investigation terminates at `AWAITING_HUMAN_REVIEW`;
  `ESCALATED` / `CLOSED` have no automated path.
* Phase 1: a SAR "starts and stays at DRAFT until a human reviewer acts".

**Decision:** `CaseService.apply_review` is the single writer of all of them,
and it requires a named `reviewer` with no default. Nothing else in the codebase
can set any of these states.

**Consequences:** The promises made in Phases 1, 3, and 5 are kept rather than
quietly abandoned when the phase that needed them arrived -- which is the usual
fate of a reserved state. "Humans make the final compliance decision" now has a
concrete meaning: exactly one function, requiring a person's name, writes every
state that represents a decision. A reviewer also cannot adjudicate another
client's match by guessing an id (subject_ref is checked), because an authority
boundary that only holds for well-behaved callers is not a boundary.

`reviewer` having no default is load-bearing: an unattributed compliance
decision is not a compliance decision, and a default of "system" would have
turned every unauthenticated call into one.

---

## ADR-036: The SAR is deterministic; the LLM writes one paragraph, last

**Phase:** 6 **Status:** Accepted

**Context:** "The LLM may assist only with narrative... The LLM must never
invent evidence" (brief SS6). The obvious implementation hands the model the
facts and asks it to produce the document, with a prompt forbidding invention.

**Decision:** Eight of the nine sections are assembled by ordinary Python from
stored rows and are COMPLETE before any model call. The Executive Summary is
generated afterwards, from those finished sections, against a schema whose only
fields are `executive_summary` (prose) and `cited_evidence_ids` (integers).
Nothing the model returns is merged into a factual section.

**Consequences:** "The LLM invented a transaction in the SAR" is not mitigated,
it is unreachable -- the narrative schema has no field that could carry a date,
an amount, an entity, or an evidence row, and the factual sections were finished
before the model was called. This is the same technique as Phase 5's containment
(ADR-028): make the bad outcome unrepresentable rather than forbidden.
`test_only_the_narrative_is_llm_generated` asserts the split per section.

Four consequences worth stating:

* **The narrative is still grounding-checked.** It cannot add evidence but it
  can still *cite* an id that does not exist, in a document that reads as a
  filing. The Phase 5 validator is reused rather than reimplemented -- two
  implementations of "is this grounded?" is two chances to disagree. A failure
  is written into the document body as a WARNING, so a reviewer sees it without
  opening the database.
* **No LLM still produces a SAR.** A SAR is a factual document whose facts are
  deterministic; the absence of a model must never be why a compliance officer
  has no draft to read. The narrative section says plainly that it could not be
  generated.
* **Reviewer Notes are never machine-populated.** A system that pre-filled them
  would be putting words in the mouth of the person accountable for the filing.
* **Approving does not close the case.** Approval means "fit to file"; filing is
  out of scope, and a case that closed itself on approval would assert an
  outcome nobody recorded.

---

## ADR-037: CLOSED is terminal; there is no reopen

**Phase:** 6 **Status:** Accepted

**Context:** Every case state machine eventually gets asked for a reopen.

**Decision:** `CaseStatus.CLOSED` has an empty transition set. No action is
permitted from it. A client may have any number of cases over time, keyed by
`case_ref`.

**Consequences:** Reopening would overwrite the fact that the case was closed --
and that fact, with the reviewer who closed it and when, is precisely what an
auditor came to see. The honest way to revisit a closed matter is a new case
referencing it, which also preserves the sequence of who decided what and when.
The error message says so rather than merely refusing, because a compliance
engineer hitting this needs the alternative, not a wall.

This mirrors the append-only discipline used throughout: risk events have no
`update` (Phase 4), investigations are never mutated on re-run (ADR-029),
reviews are never overwritten (brief SS4), and audit rows have no delete. Phase
6's contribution is applying it to the lifecycle itself.
