# Phase Log — Continuous KYC Autonomous Auditor

A running record of each implementation phase: what was inspected, what changed, what was run,
what was verified, and what remains unproven.

---

## Phase 1 — Repository inspection & dataset profiling

**Date:** 2026-07-16
**Goal:** Establish ground truth before writing any application code.

### Files inspected

- Repository tree (`data/`, 979 files) — full depth walk
- `data/kyc_profiles/clients_with_fatf_ofac.csv` (2,000 rows)
- `data/kyc_profiles/client_account_mapping.csv` (120 rows)
- `data/kyc_profiles/transactions_with_fatf_ofac.csv` (50,000 rows)
- `data/aml_transactions/SAML-D.csv` (9,504,852 rows, full scan)
- `data/sanctions/ofac_sdn.csv`, `ofac_alt.csv`, `ofac_add.csv`
- `data/sanctions/opensanctions_targets.csv` (1,319,152 rows, full scan)
- `data/articles/{clean,adverse_hit,adversarial}_article.txt` (read in full)
- `data/ubo/{simple,showcase}_structure.json` (read in full)
- `data/gdpr*`, `data/opp115/`, `data/privacy_qa/`, `data/gcapi.dll` (identified, not relevant)

### Files created

- `docs/DATASET.md` — verified dataset profile, limitations, requirement-by-requirement honesty check
- `docs/PHASE_LOG.md` — this file

### Files modified

None. No existing code was found to preserve; no data files were altered.

### Implementation summary

No application code written — by design. Phase 1 is inspection only. The repository contains **no
source code, no config, no dependency manifest, no tests**. It is a data-only tree. The only
commit in history (`d18573d initial commit`) belongs to the home-directory repo, not this project.

Key architectural conclusions reached (all evidence in `docs/DATASET.md`):

1. **The spine is the account bridge.** `client_account_mapping.csv` joins 60 clients → 120
   accounts → real rows in the 9.5 M SAML-D set. Verified: 120/120 accounts have history. This is
   the monitoring population.
2. **Client names do not resolve to real sanctions lists** (0/2000 exact matches). `sanctions_flag`
   is an upstream synthetic label, not a screening result. These two sources must stay separate and
   separately cited.
3. **Entity resolution has a genuine job.** Fuzzy matching "Mohammad Al-Rashid" against OFAC returns
   real false positives (`AL-RASHID TRUST`, `AL-RASHIDI, NAWAF AHMAD ALWAN`). The UBO record's
   `nationality` and `dob` are the real features that kill them. No fabrication needed.
4. **`adversarial_article.txt` carries a live prompt injection** targeting the risk score. The
   deterministic-scoring principle is what defeats it; the agent must treat article text as
   untrusted and log the attempt as an auditable event.
5. **The timeline cannot be back-filled honestly** — no dated KYC history exists. It must be built
   from genuinely timestamped events plus our own computed score changes going forward.

### Commands run

```bash
find . -maxdepth 3 -not -path '*/node_modules*' ...      # tree walk
git rev-parse --show-toplevel                            # → C:/Users/anike  (see issue below)
md5sum clients_with_fatf_ofac.csv kyc_profiles/...       # duplicate detection
python -c "...pandas profiling of clients/mapping/txns"  # distributions, join integrity
python -c "...chunked scan of SAML-D 9.5M rows"          # account bridge verification
python -c "...chunked scan of opensanctions 1.3M rows"   # narrative-entity probes
python -c "...importlib probe of candidate deps"         # environment inventory
```

### Test results

No test suite exists yet. Verification in this phase was empirical measurement against the data:

| Check | Result |
|---|---|
| Mapped accounts present in SAML-D | **120 / 120** ✅ |
| `transactions.client_id` ⊆ `clients.client_id` | **True** ✅ |
| `mapping.client_id` ⊆ `clients.client_id` | **True** ✅ |
| SAML-D full row count | 9,504,852 (9,873 laundering) ✅ |
| OpenSanctions rows parsed | 1,319,152 ✅ |
| Client names matching OFAC SDN exactly | **0 / 2000** ⚠️ documented |
| Narrative entities in real sanctions lists | **0 hits** ⚠️ documented |

### Known limitations

1. **Adverse media is 3 files**, not a feed, and none are linked to a `client_id`. The pipeline will
   be real; the volume is a fixture set. Not padding it with generated articles.
2. **No historical risk data** — timeline is forward-looking + genuinely-timestamped events only.
3. **`trade_mispricing_flag` has 10 rows** (0.02%) — too sparse to carry a scoring band alone.
4. **Two disjoint transaction universes** — SAML-D (from 2022-10) and
   `transactions_with_fatf_ofac.csv` (2025-07 → 2025-09) share only account numbers, not a timeline.
5. **1,940 of 2,000 clients have no account mapping** — they have profile flags and the 50k-row
   txn file, but no SAML-D behavioural depth.
6. **~190 MB of the tree is an unrelated privacy/GDPR project** (`opp115/`, `privacy_qa/`, `gdpr*`,
   `gcapi.dll`). Ignoring, not deleting.
7. `sanctions_flag=1` clients (55) cannot be corroborated against any real list.

### Recommended next step

Two decisions are needed before Phase 2 starts.

**A. Git repository scope — needs your call.**
The git root is `C:/Users/anike`; your whole home directory is the repo. Committing from here
would sweep in `.ssh/`, `AppData/`, `NTUSER.DAT`, and browser profiles. Options: (1) `git init` a
fresh repo at `Desktop/ds project/techm` — clean and recommended; (2) add a scoped `.gitignore`;
(3) leave it and never commit. I have not run any git write command and will not until you choose.

**B. Phase 2 scope — proposed.**
*Foundation: ingestion → validation/normalization → persistence.* Concretely: a `pyproject.toml`
and package skeleton; Pydantic models for client / account / transaction / sanctions-target /
article / UBO node+edge; loaders reading the real files with the duplicate-path question settled;
a normalization layer (country codes, name canonicalization, timestamp parsing); SQLite via
SQLAlchemy for the durable store + audit tables; and a pytest suite asserting the invariants
already measured above (120/120 bridge, subset joins, row counts) so regressions surface
immediately.

Deliberately **not** in Phase 2: entity resolution, agents, scoring. Those land once the data
layer is proven.

---

**Numbering note:** the entry above predates the project's standardized phase numbering. It is
the work now formally referred to as **Phase 0** (see `docs/phase-0-dataset-audit.md`,
`docs/data-dictionary.md`, `docs/system-design-phase-0.md`). Everything from this point on uses
the canonical numbering (Phase 0 = dataset audit, Phase 1 = backend foundation, ...).

---

## Phase 1 — Backend Foundation

**Date:** 2026-07-16
**Goal:** Build the FastAPI/SQLAlchemy/SQLite backend foundation and contracts future phases
implement against, per `docs/phase-0-dataset-audit.md` and `docs/system-design-phase-0.md`.

### Files inspected

- `docs/phase-0-dataset-audit.md`, `docs/data-dictionary.md`, `docs/system-design-phase-0.md`,
  `docs/DATASET.md`, `docs/PHASE_LOG.md` (this file) — re-read as source of truth before coding.
- Repository state re-verified: no application code existed prior to this phase; only `data/`,
  `docs/`, `scripts/` were present.
- Installed dependency inventory (fastapi, uvicorn, sqlalchemy, pydantic, pandas, rapidfuzz,
  pytest, httpx already present; `pydantic-settings` installed as the one new dependency).

### Files created

39 files under `backend/` (`app/` — 12 subpackages, `tests/` — 9 test modules + conftest,
`requirements.txt`, `.env.example`, `README.md`, `init_db.py`), plus `README.md` at the project
root and `docs/phase-1-foundation.md`. Full structure and rationale in
`docs/phase-1-foundation.md` SS2.

### Files modified

`docs/PHASE_LOG.md` (this entry). No files under `data/` were read-modified, moved, or deleted.

### Implementation summary

Built: configuration (zero-secret defaults), SQLAlchemy 2.x database foundation (FK enforcement,
non-destructive `init_db`), 19 ORM models across 12 files, matching Pydantic schemas, a dataset
source registry (16 sources, Tier-1/Tier-2 sanctions kept explicitly separate), ingestion
validation contracts (bounded-sample validators, never a full read of a large file), an audit
service with safe serialization/truncation, and a FastAPI app with health/sources/providers
endpoints.

**Mid-phase architecture change:** an explicit directive required the system be built so it is
never hardcoded to the Phase 0 dataset's specific clients or demo entities. This added a
provider/adapter architecture (`app/providers/`): five entity-agnostic Protocol contracts
(Sanctions/AdverseMedia/CorporateRegistry/Transaction/Ownership), normalized response schemas
(`ExternalEntityCandidate`, `ExternalArticle`, `ProviderResult[T]` with a graceful-degradation
status enum), one real generic local-fixture sanctions provider (rapidfuzz-based, zero
entity-specific code, proven against both a nonsense name and a known fixture name), three honest
"not yet implemented" placeholders for the three planned live API integrations (news/adverse
media, sanctions, corporate registry — real config plumbing, zero fake network calls), and a
`ProviderRegistry` proving the hybrid local+external design within one category. Full detail in
`docs/phase-1-foundation.md` SS6.

### Commands run

```bash
pip install "pydantic-settings>=2.0,<3.0"
python -c "...smoke-test model registration + Base.metadata.create_all"
python -c "...smoke-test provider registry, local sanctions provider, pending providers"
python -c "...smoke-test source registry against real data dir"
python -c "...smoke-test validate_all_sources against real data dir + temp DB"
python -c "...smoke-test audit service incl. truncation"
python -c "...full TestClient boot: health/live, health/ready, sources, providers"
python -m pytest tests/ -v
python init_db.py
```

### Test results

**42 / 42 passed in 0.80s.** Full breakdown in `docs/phase-1-foundation.md` SS13. Highlights:
SQLite FK violation correctly raises `IntegrityError`; SAML-D (951 MB) and OpenSanctions (488 MB)
header validation both complete in well under 5 seconds (empirical proof neither large file is
read in full); `validate_all_sources` never reaches `LOADED` and creates zero `Transaction` rows;
the local sanctions provider is proven generic (nonsense name → `NO_RESULTS`, known fixture name →
`SUCCESS`, identical code path); a dynamically-registered dummy provider appears in the registry
immediately; `/api/v1/providers` never leaks a configured API key.

### Known limitations

See `docs/phase-1-foundation.md` SS14 for the full list. Headline items: no data has been ingested
into any table yet (every model is schema-only); `EntityMatch`/`Evidence` polymorphic associations
are not FK-enforced (documented tradeoff); the three pending API providers make zero network calls
by design; ingestion idempotency keys are documented but not implemented.

### Recommended next step

Phase 2: implement real ingestion for the small, canonical-path sources (`clients`,
`client_account_mapping`, `transactions_shallow`, both UBO fixtures, both curated sanctions
fixtures, all three articles) using the natural keys already specified in
`app/ingestion/base.py`, then build the Entity Resolution Service against the now-real
`LocalCuratedSanctionsProvider` and `SanctionsEntity`/`EntityMatch` tables. Chunked SAML-D and
Tier-1 OFAC/OpenSanctions ingestion, scoped to the 120 mapped accounts and to on-demand lookups
respectively, should follow once the smaller sources are proven end-to-end.

---

## Phase 2 -- Ingestion, Normalization, and Customer 360

**Date:** 2026-07-16
**Goal:** Build the real ingestion/normalization/repository/Customer 360 data layer on Phase 1's
schema and provider architecture, per `docs/phase-2-ingestion.md`.

### Files inspected

`docs/phase-0-dataset-audit.md`, `docs/system-design-phase-0.md`, `docs/phase-1-foundation.md`,
and the full existing `backend/app/` tree (models, providers, registry, core) were re-read before
writing any code, to avoid duplicating or contradicting Phase 1 decisions.

### Files created

~30 new files: `app/ingestion/normalizers.py`; 6 loaders + loader registry
(`app/ingestion/loaders/`); `app/ingestion/commands.py`; 8 repositories
(`app/repositories/`); 4 new providers (`app/providers/tier1_ofac_provider.py`,
`tier1_opensanctions_provider.py`, `saml_d_transaction_provider.py`,
`local_adverse_media_provider.py`); `app/services/provider_execution_service.py`,
`customer360_service.py`; `app/schemas/customer360.py`, `ingestion.py`; 3 new API route modules
(`ingestion.py`, `customers.py`, `datasets.py`); 15 new test files (114 tests total across the
suite); `docs/phase-2-ingestion.md`; `docs/ARCHITECTURE_DECISIONS.md`.

### Files modified

`app/registry/sources.py` (5 sources' `ingestion_strategy` corrected to `LOOKUP_ONLY` to match
what Phase 2 actually implemented), `app/providers/registry.py` (4 new providers registered),
`app/core/database.py` (WAL mode + `synchronous=NORMAL` PRAGMAs -- ADR-010), `app/models/transaction.py`
(composite unique index -- ADR-006), `app/repositories/transaction_repository.py` (Integer cast
fix -- ADR-007), `app/ingestion/validate_all.py` (refactored to use the new
`DatasetSourceStatusRepository`; validation no longer downgrades an already-LOADED source's
status), `app/ingestion/results.py` (2 new `IngestionResultStatus` values, additive), `app/main.py`
(3 new routers wired in), `app/api/deps.py` (2 new dependencies). No file under `data/` was
read-modified, moved, or deleted.

### Implementation summary

Real, idempotent upsert ingestion now exists for all 10 small/curated Phase 0 sources (~43s full
pipeline, dominated by the 50,000-row shallow transaction file). SAML-D and the three Tier-1
sanctions files are deliberately never bulk-loaded -- four new streaming providers serve them
live instead, reusing Phase 1's provider Protocol architecture rather than inventing a parallel
mechanism. `Customer360Service` assembles a normalized profile from the database (fast, default)
plus three independently opt-in live-provider lookups (slow, explicit). A `ProviderExecutionService`
gives every provider call uniform timeout/retry/error handling. Full detail in
`docs/phase-2-ingestion.md`.

**Three real bugs were found and fixed by actually running the code against real data, not just
unit-testing in isolation** -- full root-cause detail in `docs/ARCHITECTURE_DECISIONS.md`
ADR-006/007/008:
1. A SQLite query-planner misselection turned the 50,000-row transaction upsert into an unbounded
   hang (285s+, killed before completing) -- fixed with a composite index matching the actual
   natural key.
2. `TransactionRepository.summary_for_client`'s `flagged_count` silently returned Python `True`
   instead of `22` for a real client, due to SQLAlchemy's Boolean-typed `SUM()` result coercion --
   fixed with an explicit `cast(..., Integer)`.
3. `ProviderExecutionService`'s timeout handling correctly labelled a result `TIMEOUT` but the
   calling thread still blocked for the full hang duration, because `ThreadPoolExecutor`'s context
   manager waits on exit -- fixed with explicit `shutdown(wait=False)`.

### Commands run

```bash
python -c "...end-to-end ingestion pipeline against real data, multiple iterations while debugging"
python -c "...isolated performance benchmarks: raw sqlite3 vs ORM, SELECT-only vs INSERT-only,
            growing vs fixed table size, EXPLAIN QUERY PLAN"                    # root-caused ADR-006
python -c "...Customer360Service smoke tests against real ingested data + fake/real providers"
python -c "...ProviderExecutionService smoke tests: flaky/hanging/raising/unconfigured providers"
python -c "...full TestClient boot: ingestion validate/load, customers, customers/360, datasets/status"
python -m pytest tests/ -v          # 114 passed in 174.31s
```

### Test results

**114 / 114 passed in ~174s** (up from Phase 1's 42; the increase is almost entirely one
deliberately comprehensive real-data integration test plus the Tier-1 OpenSanctions provider test,
both intentionally isolated to run once rather than repeated across the suite). Full breakdown in
`docs/phase-2-ingestion.md`. Highlights: the real 50,000-row transaction file ingests correctly and
idempotently; the known Sokolov column-shift defect (Phase 0) is caught by a generic heuristic, not
a row-specific check; all four large-dataset providers are proven against the real files with
empirical timing guards; the boolean-SUM and timeout bugs both have regression tests; Customer 360
for the real client_id=3 ("Phillips-Hanson") returns the exact counts Phase 0 measured (25
transactions, 22 flagged, 2 accounts).

### Known limitations

See `docs/phase-2-ingestion.md` SS9 for the full list. Headline items: no entity resolution exists
(provider hits are unconfirmed candidates, never called matches); `Tier1OfacLookupProvider` doesn't
match `ofac_alt.csv` aliases yet; no persistent search index for the Tier-1 files (linear stream
per query); `Evidence`/`EntityMatch` remain schema-only; ingestion is synchronous over HTTP (no
task queue, by design).

### Recommended next step

Phase 3: Entity Resolution Service -- consume the now-real `SanctionsEntity`/`SanctionsAlias`
tables and the Tier-1/Tier-2 providers' candidates, add corroboration scoring (entity_type/
nationality/DOB agreement, per the approach validated in `docs/phase-0-dataset-audit.md` SS6),
and start writing real `EntityMatch`/`Evidence` rows instead of leaving them schema-only.

---

## Phase 3 -- Entity Resolution & Evidence Engine

**Date:** 2026-07-16
**Goal:** Build a generic, production-quality entity-resolution pipeline and evidence engine on
Phase 2's data layer, per `docs/phase-3-entity-resolution.md`.

### Files inspected

`docs/phase-0-dataset-audit.md` (esp. SS6 entity-resolution feasibility and SS14 calibration
limits), `docs/phase-1-foundation.md`, `docs/phase-2-ingestion.md`, `docs/ARCHITECTURE_DECISIONS.md`,
and the existing `EntityMatch`/`Evidence` models before changing either.

### Files created

`app/resolution/` (schemas, normalization, config, adapters, candidates, confidence, pipeline,
`scorers/` x3), `backend/config/resolution_weights.json`,
`app/services/entity_resolution_service.py`, `app/services/evidence_service.py`,
`app/repositories/entity_match_repository.py`, `app/schemas/resolution.py`,
`app/api/routes/entity_resolution.py`, `app/api/routes/evidence.py`, 6 test modules
(101 new tests), `docs/phase-3-entity-resolution.md`.

### Files modified (all additive)

`app/core/enums.py` (EntityMatchStatus += POSSIBLE/HIGH_CONFIDENCE; EvidenceType +=
PROVIDER_RESPONSE/MANUAL), `app/core/config.py` (+resolution_weights_path),
`app/models/resolution.py` (candidate FK now nullable + provider/attribute/reason columns --
ADR-014), `app/models/evidence.py` (+structured_facts, +entity_match_id),
`app/repositories/evidence_repository.py` (+list_for_entity_match), `app/main.py` (2 routers),
`docs/ARCHITECTURE_DECISIONS.md` (ADR-011..016), `CLAUDE.md`. No file under `data/` touched.
No Phase 2 ingestion logic changed.

### Implementation summary

Nine independently-testable scorers returning three-state results (agreement / contradiction /
absent -- ADR-012), RapidFuzz metric selected by entity type, a deterministic confidence engine
with config-loaded weights (ADR-011) that penalizes conflicts twice (ADR-013), blocking-based
candidate generation that never touches the 1.3M-row provider unless explicitly asked, a pure
non-writing pipeline (ADR-015), and an evidence engine with all six required evidence kinds plus
the FK-based evidence graph.

**The engine reproduces Phase 0's SS6 prediction exactly, measured:** true hit
`AL-RASHID, Mohammad` -> 89.2 HIGH_CONFIDENCE; false positive `AL-RASHID TRUST` -> 29.4
AUTO_REJECTED (entity-type conflict); false positive `AL-RASHIDI, NAWAF AHMAD ALWAN` -> 29.8
AUTO_REJECTED (nationality conflict + name floor). All three are regression tests.

One self-caught defect during development: `_local_sanctions_id()` was written as a stub that
always returned None -- fake functionality. Fixed properly by carrying `internal_id` through
`ResolutionSubject` from the adapters, so a DB-sourced candidate gets a real FK and a
provider-sourced one correctly gets NULL (ADR-014), verified by test.

### Commands run

```bash
python -c "...scorer verification against the real Phase 0 false-positive cases"
python -c "...confidence verdicts: TRUE HIT vs 2 known FPs vs unrelated"
python -c "...end-to-end: ingest real data -> resolve UBO person -> evidence graph -> idempotency"
python -c "...full TestClient: resolve-pair, resolve, batch, matches, evidence, 404/422"
python -m pytest tests/ -q        # 215 passed
```

### Test results

**215 / 215 passed in 115s** (114 -> 215; +101 Phase 3 tests). No regressions in Phase 1/2.
Highlights: the three real false-positive regressions; an exhaustive test proving the engine
cannot reach CONFIRMED/HUMAN_REVIEWED (ADR-016); a spy provider proving the 1.3M-row provider is
never queried by default; a config test proving identical inputs give a different confidence
under a different weights file; and an honest test asserting a real Client produces **no**
HIGH_CONFIDENCE match against sanctions data -- reproducing Phase 0's 0/2000 finding rather than
manufacturing a hit.

### Known limitations

See `docs/phase-3-entity-resolution.md` SS11. Headline: blocking is a real recall trade (names
sharing no significant token aren't retrieved); Clients rarely exceed CANDIDATE because the client
master has no DOB/nationality/identifiers to corroborate with (Phase 0's finding, reproduced not
hidden); weights are expert-set, not calibrated; no transitive clustering; batch is sequential.

### Recommended next step

Phase 4: the deterministic risk-scoring engine. It should consume `Evidence` rows (now real) and
populate `RiskEvent`/`RiskScoreSnapshot` (still schema-only), keeping the same discipline this
phase used: weights in configuration, arithmetic deterministic and reproducible, every score
traceable to the evidence that produced it, and no LLM anywhere near the authoritative number.

---

## Phase 4 -- Continuous Monitoring & Explainable Risk Intelligence

**Date:** 2026-07-16
**Goal:** Turn the project from a static KYC lookup tool into an event-driven Continuous KYC
platform, per `docs/phase-4-risk-intelligence.md`.

### Files inspected

All Phase 0-3 docs and ADR-001..016, plus the existing `RiskEvent`/`RiskScoreSnapshot`/`Alert`
models before touching any of them. Key reuse decision made from that reading: `RiskScoreSnapshot`
already carried previous/current/computed_at/trigger_reason/triggering_events, so it **is** the
risk history -- Phase 4 extended it (`delta`, `previous_band`, `factor_contributions`) rather than
building a parallel table.

### Files created

`backend/config/risk_factors.json` (12 factors), `app/risk/` (config, schemas, engine, signals,
alerts), `app/repositories/risk_repository.py`, `app/services/monitoring_service.py`,
`app/api/routes/{monitoring,risk,alerts}.py`, 4 test modules (87 new tests),
`docs/phase-4-risk-intelligence.md`.

### Files modified (all additive)

`app/core/enums.py` (RiskEventType += 7 values; new AlertTrigger), `app/core/config.py`
(+risk_factors_path), `app/models/risk.py` (RiskEvent += dedup_key/source/trigger/summary/
entity_ref/factor_id + unique constraint; Snapshot += previous_band/delta/factor_contributions),
`app/models/alert.py` (+severity/trigger/reason/risk_delta/dedup_key + 2 M2M tables),
`app/schemas/risk.py`, `app/repositories/evidence_repository.py` (+get_by_id), `app/main.py`
(3 routers), `docs/ARCHITECTURE_DECISIONS.md` (ADR-017..022), `CLAUDE.md`, `README.md`.
No file under `data/` touched. No Phase 2/3 ingestion or resolution logic changed.

### Implementation summary

A config-driven Risk Factor Registry (12 factors, expandable with **zero code changes** -- proven
by a test that invents a factor with an unseen signal_type), a deterministic risk engine
reproducing the brief's worked example exactly (**weight 50 x confidence 0.82 = 41.0**), immutable
RiskEvents with dedup-key change detection, an append-only snapshot history, a change-triggered
alert engine, and a MonitoringService that survives provider failure.

**Two real defects found by running the code, not by unit tests:**
1. **A false alert (ADR-020).** The first live cycle emitted *"2 new OTHER findings in one
   monitoring cycle"* -- `high_risk_sector` and `ownership_opacity` both map to the `OTHER`
   catch-all, so two unrelated facts were reported to an analyst as repetition. Fixed via config
   (`repeated_signal_excluded_event_types`), tested both ways. Only visible by reading the alert
   text a human would actually receive.
2. **A Phase 1 schema regression.** The full-suite run caught
   `test_pydantic_schema_serializes_enum_as_plain_string` failing: my new snapshot fields were
   required in the Pydantic schema but nullable in the DB. The schema was wrong, not the test --
   fixed with proper defaults rather than editing the assertion.

Also self-caught during development: a `hasattr(self._evidence, "get_by_id")` guard hedging around
a method that didn't exist. Added the method properly instead.

### Commands run

```bash
python -c "...risk engine vs the brief's 50 x 0.82 = 41 worked example"
python -c "...end-to-end monitoring cycle x2 on real data -> change detection"
python -c "...full TestClient: monitor, risk, history, factors, events, alerts, 404s"
python -m pytest tests/ -q          # 302 passed
```

### Test results

**302 / 302 passed in 146s** (215 -> 302; +87 Phase 4 tests). No regressions.
Highlights: the brief's worked example pinned; a config-invented factor scoring correctly with no
code change; cycle 2 on unchanged data suppressing 3/3 duplicates with an identical score; a
provider that *raises* not breaking the cycle; provider failure recorded with **zero** risk weight;
the AST-level guard proving the risk engine imports no LLM SDK, HTTP client, or DB session; and the
real-dataset regression pinning client 3 at exactly **53.0 HIGH** (40 + 8 + 5).

### Known limitations

See `docs/phase-4-risk-intelligence.md` SS9. Headline: weights are expert-set not calibrated;
monitoring is synchronous (no task queue by design); parallelism is across provider categories not
clients (single-writer SQLite -- ADR-001); most clients can't exceed profile-driven factors because
Phase 0's 0/2000 name-match finding still holds; deep SAML-D behavioural scoring isn't wired into
the cycle (~18s/account is not viable per-cycle).

### Recommended next step

Phase 5: the LLM investigation agent + timeline. It must consume the now-real `RiskEvent` /
`Evidence` / `RiskScoreSnapshot` rows and write only to `Investigation`/`InvestigationFinding` --
never to `current_score`. `data/articles/adversarial_article.txt`'s embedded prompt injection is
the standing acceptance test for that boundary, and the deterministic engine built here is what
makes the boundary enforceable rather than aspirational.

---

## Phase 5 -- Autonomous Investigation Engine

**Status:** complete. The first phase permitted to call an LLM.
Full writeup: `docs/phase-5-investigation-agent.md`. ADRs 023-029.

### What was built

An `Alert -> Context -> LLM -> Grounded findings -> Recommendations -> Human`
pipeline in which exactly one step is non-deterministic. `app/investigation/`
(schemas, context, prompts, grounding, agent), `app/providers/` (LLMProvider
Protocol + a real Anthropic client + registry), `InvestigationOrchestrator`,
5 endpoints, and 3 tables (one new: `investigation_recommendations`).

### The boundary, enforced structurally

`agent.py`'s AST is asserted to import no `sqlalchemy`, no `app.risk`, no
`app.resolution`, no `app.repositories`, no `app.services`, no `app.models`.
This is the **mirror of Phase 4's `test_engine_imports_no_llm_or_io`**: the
scorer cannot reach a model, and the model-caller cannot reach the scorer. The
recommendation vocabulary has no APPROVE/REJECT at four layers (JSON Schema
enum, Pydantic, DB column, validator), and `ESCALATED`/`CLOSED` have no code
path at all -- the repository exposes no method that could set them.

### Two honesty calls

**Temperature is null.** The brief asks for it; current models *reject*
sampling parameters with HTTP 400 rather than defaulting them, so none is sent.
Recording `0.0` would fabricate a request parameter that was never transmitted
(ADR-025).

**Chain-of-thought is never stored because it is never requested.**
`thinking.display` is pinned to `"omitted"` -- thinking stays on, no transcript
comes back. "We don't store X" became "we never receive X" (ADR-026).

### Bugs found by running it, not by unit tests

1. **`context_hash` included `trigger_reason`.** A re-run's reason embeds the
   original's id, so every re-run produced a different hash and could never
   report "evidence unchanged" -- the mechanism broken exactly where it is used.
   Same class as Phase 4's dedup-key-with-a-timestamp trap (ADR-019); the
   docstring warned about it while the code committed it.
2. **Pydantic was more permissive than the JSON schema**, defaulting `reasoning`
   and `confidence_statement` to `""`. Gate 2 exists to catch a provider whose
   constrained output leaked, so it must not be weaker than gate 1.
3. **A test grepped source and passed by matching its own docstring.** Replaced
   with AST inspection of the real call.

### Test results

**376 / 376 passed in 288s** (302 -> 376; +74 Phase 5 tests). No regressions.
Highlights: the real `adversarial_article.txt` payload detected on all four
patterns, quarantined, and preserved **verbatim**; the model narrating "no risk
whatsoever" while the stored score stays **53.0**; a hallucinated citation
caught, flagged, and stored rather than dropped; APPROVE rejected even with the
schema bypassed; every provider failure mode degrading to a recorded FAILED with
no fabricated report; and the whole pipeline running on a non-Anthropic provider
unchanged.

### Live verification

`POST /monitor/client/3` -> `score=53.0 band=HIGH` -- identical to Phase 4; the
investigation layer changed nothing deterministic. `POST /investigations/run/3`
with no key -> `FAILED`, no report, actionable reason, 200 not 5xx. SQLite
388 KB; 24 tables; `data/` untouched (979 files, 1.70 GB).

### Known limitations

See `docs/phase-5-investigation-agent.md` SS11. **Headline: the successful-response
path has never run against the live API** -- no credentials exist on this
machine. Request construction, transport, and every failure path *are* verified
end-to-end (a real request with an invalid key reached api.anthropic.com,
returned a `request_id` + 401, and mapped correctly to NOT_CONFIGURED); parsing
a 200 into a validated report is covered only by test doubles. Also: ownership
is always empty (Phase 0 SS5 -- no linkage exists to load, and faking one would
be the worst kind of guess); context is bounded at 40 evidence items;
investigation reads stored state rather than re-querying providers; injection
detection is regex-based and is a recorder, not the control.

### Recommended next step

**Run one investigation with a real `LLM_API_KEY`** -- the single outstanding
verification, and the only way to close the gap above. Then Phase 6: the risk
timeline, which can now consume `Investigation` rows alongside the Phase 4
`RiskEvent`/`RiskScoreSnapshot` history. SAR drafting and the human-review
workflow should follow the same boundary this phase established: the agent
drafts, `SARDraft`/`HumanReview` stay human-owned, and no automated path may
reach a compliance decision.

---

## Phase 6 -- Enterprise Case Management

**Status:** complete. Full writeup: `docs/phase-6-case-management.md`. ADRs 032-037.

### What was built

The compliance workflow after an investigation: `Case` workspace, generated timeline, human
review workflow + validated state machine, Draft SAR generator, immutable audit trail, and
case metrics. `app/casework/` (state_machine, timeline, sar, schemas), `CaseService`,
`CaseRepository`, 9 endpoints, 1 new table (`cases`), `HumanReview`/`SARDraft` filled in
exactly as Phase 1 reserved them.

### The phase the reserved states were waiting for

Phases 1, 3 and 5 each made a state unreachable and wrote that a later phase would reach it.
This is that phase, and `CaseService.apply_review` is the SINGLE writer of all of them:
EntityMatch CONFIRMED/HUMAN_REVIEWED (ADR-016), Investigation ESCALATED/CLOSED (ADR-029),
SAR APPROVED/REJECTED (Phase 1). The authority is always a named `reviewer` with no default --
an unattributed compliance decision is not a compliance decision (ADR-035).

### Three design calls

**A Case is an anchor, not a copy** (ADR-032). It stores lifecycle only -- no score, no
summary. A workspace showing a stale score beside a live investigation is worse than none.

**The timeline is generated and has no `add_entry`** (ADR-033). `build` is the entire public
surface; nine collectors project stored rows. A timeline you can append to is one someone can
append to incorrectly.

**The SAR's LLM writes one paragraph, last** (ADR-036). Eight of nine sections are complete
deterministic Python BEFORE any model call; the narrative schema has no field that could carry
a date, an amount, or an evidence row. "The LLM invented a transaction" is unreachable, not
forbidden.

### Bugs found by running it

1. **SQLite returns naive datetimes** even from `DateTime(timezone=True)`. Sorting naive
   against aware raises TypeError -- a timeline mixing them crashes on the first case that has
   both, i.e. every real case. `_utc()` normalises at the collector boundary.
2. **Validate-then-mutate-then-record ordering** (ADR-034): a review row written for a
   transition that was rejected would be a lie in the audit trail. Pinned by
   `test_an_illegal_action_writes_nothing_at_all`.

### Test results

**476 / 476 passed in 244s** (400 -> 476; +76 Phase 6). No regressions. Highlights: an illegal
action writing *nothing*; reviews append-only across a mind-change; CONFIRM_MATCH as the only
route to CONFIRMED; a reviewer blocked from another client's match; the timeline deterministic
across two reads; SAR sections proven deterministic except the narrative; a hallucinated
narrative flagged *in the document*; a SAR generated with no LLM at all; and the API exposing
no path that decides.

### Known limitations

See `docs/phase-6-case-management.md` SS11. Headline: **no authentication** -- `reviewer` is a
caller-supplied string, so the workflow records who *claimed* to decide and nothing verifies
it. Also: no SAR filing and no FILED state; timeline unpaginated (200/source); audit
correlation is by target, not a correlation-id graph; per-case queries would need
denormalised counters at 10k+ cases -- which is precisely the trade ADR-032 refuses today.

### Recommended next step

Authentication + reviewer identity, before anything else: every guarantee in this phase rests
on `reviewer` naming a real, verified person. Then Phase 7 (frontend/dashboard), which
`GET /cases/metrics` and `GET /cases/{id}/timeline` already exist to serve.

---

## Phase 7 -- Enterprise Frontend

**Status:** complete. Writeup: `docs/phase-7-frontend.md`. No backend change.

### What was built

React + Vite + TypeScript + Tailwind + shadcn-style components + TanStack Query + React Router
+ Recharts + Lucide. 9 lazy-loaded pages, 12 reusable domain components, 4 charts, one HTTP
boundary, **20 real backend endpoints consumed** (all verified 200 live).

### The rule that shaped every page

The backend works hard to distinguish facts that look alike; a UI can undo all of it. So:
`RiskBadge` shows **"Not assessed"** and never 0/LOW for an unscored client; `TierBadge` marks
Tier-2 demo data non-authoritative everywhere it appears (ADR-002); `null` renders `--` and `0`
renders `0`; SYSTEM/AGENT/HUMAN get visually distinct chips; fabricated citations are shown
**before** the prose; and the review form is driven entirely by the server's `available_actions`
so the UI can never offer an action the backend rejects.

### Honest gaps, labelled in the UI itself

`/customers` has no search/country/sort and `ClientRead` has no risk score, so those filters are
client-side over the loaded page — and a banner says so, because a page-scoped search that looked
global would let a reviewer conclude a customer doesn't exist. Risk distribution is the client
master's **sector-risk label**, not the engine's computed band (no aggregate exists; deriving it
would be 2,000 requests). Audit is case-scoped (no global endpoint). API latency is measured
browser-side and labelled as such.

### A real backend defect this found

`POST /cases` -> **500 `no such column: human_reviews.case_id`** on any DB created before Phase 6.
`init_db.py`'s `create_all()` adds tables, never columns. **No test can catch it** — tests build a
fresh DB every run. Phase 6 migration gap; recorded as a trap. Real fix: Alembic.

### Test results

**21/21 frontend tests in ~6s**; `tsc --noEmit` clean; `vite build` succeeds with per-page code
splitting. Live: `monitor 53.0 HIGH` -> `CASE-000001 OPEN` -> `review 200` -> `sar 201` ->
timeline **7 entries / 5 types**. Backend suite still **476/476**.

Two testing findings: vitest's `forks` pool times out on this path (it contains a space) --
pinned to `threads`; and the query hooks' explicit `retry` overrides a test client's
`retry:false`, so error-state tests must allow for real backoff.

### Recommended next step

Three small backend additions would remove most of the UI's honest gaps: `q`/`country`/`sort` on
`/customers`, a `/risk/distribution` aggregate, and a global `/audit`. Then Alembic, then auth --
which remains the headline gap for the whole product.
