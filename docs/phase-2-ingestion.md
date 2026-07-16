# Phase 2 -- Ingestion, Normalization, and Customer 360

**Continuous KYC Autonomous Auditor**
**Status:** complete. Builds the real data layer on top of Phase 1's schema
and provider architecture. No entity resolution, risk scoring, investigation,
timeline, agents, SAR, or frontend -- those remain future phases.

---

## 1. Architecture

Phase 1 built the contracts (models, provider Protocols, registry). Phase 2
makes them real:

```
Small/curated Phase 0 files ──▶ Loaders (app/ingestion/loaders/) ──▶ Repositories ──▶ SQLite
                                     (idempotent upsert, normalized)

Large/Tier-1 Phase 0 files  ──▶ Providers (app/providers/) ──▶ ProviderExecutionService
                                     (streaming, never persisted)         (timeout/retry)
                                                                                │
                                                                                ▼
                                                              Customer360Service ──▶ Customer360Response
                                                                     (reads repositories +
                                                                      opt-in provider calls)
```

Every future component -- a risk-scoring engine, an investigation agent --
consumes the same normalized shapes regardless of whether the underlying
fact came from a bulk-ingested CSV row or a live provider lookup: ORM models
(read via repositories) for ingested data, `ExternalEntityCandidate` /
`ExternalArticle` / `ProviderResult[T]` (Phase 1) for provider data. This is
the concrete meaning of the Phase 2 objective statement.

---

## 2. What Was Ingested (real upsert, into SQLite)

| Source | Loader | Real result |
|---|---|---|
| `clients` | `ClientLoader` | 2,000 created |
| `client_account_mapping` | `AccountLoader` | 120 created (requires clients first) |
| `transactions_shallow` | `ShallowTransactionLoader` | 50,000 created |
| `sample_ofac_sdn` (+ `sample_ofac_alt`, auxiliary) | `CuratedOfacLoader` | 17 entities, 15 aliases |
| `sample_opensanctions` | `CuratedOpenSanctionsLoader` | 22 created, 1 row flagged (see SS4) |
| `article_clean/adverse_hit/adversarial` | `ArticleLoader` | 1 each, verbatim |
| `ubo_simple`, `ubo_showcase` | `OwnershipLoader` | 3+4 entities, 2+3 edges |

**Full real pipeline measured time: ~43s**, entirely dominated by the
50,000-row shallow transaction file (~41s of it -- see SS3 for why, and how
it was fixed). Every other loader completes in under 2 seconds.

Every loader:
- Reads its path from the registry, never a hard-coded string.
- Upserts on the natural key documented in `app/ingestion/base.py`
  (decided in Phase 1, implemented here).
- Is idempotent -- re-running `ingest_all()` twice against the same data
  produces the same row counts (proven in
  `tests/test_ingestion_pipeline.py`).
- Records in-file duplicate natural keys and unresolvable foreign keys as
  `IngestionError` entries, never silently drops or crashes on them.

---

## 3. Large-Dataset Strategy -- Measured, Not Just Designed

Per the Phase 2 brief and the standing project rule, SAML-D, OFAC, and
OpenSanctions are **never bulk-loaded into SQLite**. `app/registry/sources.py`
marks all five Tier-1/large sources `ingestion_strategy=LOOKUP_ONLY`, and
`app/ingestion/loaders/registry.py` has no loader entry for any of them --
there is no code path that could accidentally bulk-load them.

Instead, four providers (Phase 1's architecture, Phase 2's implementation)
serve them live, streaming in bounded chunks:

| Provider | File | Real measured cost |
|---|---|---|
| `Tier1OfacLookupProvider` | ofac_sdn.csv, 19,157 rows / 5.3 MB | ~0.7s per search |
| `Tier1OpenSanctionsLookupProvider` | opensanctions_targets.csv, 1.3M rows / 488 MB | ~40-45s per search (full scan) |
| `SamlDTransactionProvider` | SAML-D.csv, 9.5M rows / 951 MB | ~2.6s with `limit=20` (early-exit); ~18s unlimited for one account |
| `LocalCuratedAdverseMediaProvider` | 3 article fixtures, ~5 KB total | effectively instant |

All four:
- Use `pandas.read_csv(..., chunksize=N)` -- peak memory is O(chunk size),
  never O(file size). Empirically proven in
  `tests/test_ingestion_validation.py` (header validation) and
  `tests/test_large_dataset_providers.py` (full provider searches), both
  timing-bounded against the real files.
- Never write a row to SQLite.
- Degrade to `NOT_CONFIGURED`/`ERROR` rather than crashing if the file is
  missing or malformed.

**Known limitation, stated honestly:** these are linear streaming searches,
not persistent indexes. "Indexed searches where possible" from the brief is
only partially achieved -- there is no on-disk search index (e.g. a trigram
or inverted index) for the Tier-1 files. Given the measured costs above are
acceptable for an opt-in, occasional lookup (see SS5), building a real index
was judged not worth the added complexity for this phase. `Tier1OfacLookupProvider`
also does not yet match against `ofac_alt.csv` aliases (only `SDN_Name`) --
deferred for the same reason; see the module docstring for detail.

---

## 4. Normalization

`app/ingestion/normalizers.py` -- pure functions, unit-tested in isolation
(`tests/test_normalizers.py`), used identically by every loader and every
file-reading provider:

`normalize_country_code`, `normalize_currency_code`, `normalize_name`,
`normalize_entity_type`, `normalize_transaction_direction`,
`normalize_percentage`, `normalize_datetime`, `normalize_bool_flag`,
`extract_dob_from_remarks` (a generic regex extraction from OFAC's
semi-structured `Remarks` field -- not entity-specific, verified against the
real Tier-2 fixture: `AL-RASHID, Mohammad`'s Remarks correctly yields
`1975-03-15`), and `build_provenance` (the shared provenance-stamping
helper).

**A real, generic data-quality catch:** `CuratedOpenSanctionsLoader` detects
the column-shift defect Phase 0 found in `sample_opensanctions.csv` (the
Sokolov row, missing a delimiter) using a **generic heuristic** -- "does the
`dataset` field look like a dataset tag or like a date?" -- not a check for
that specific row. It still ingests the row's reliable leading fields
(id, name, schema, birth_date, countries) and nulls the unreliable shifted
fields rather than storing wrong data. Verified against the real file in
`tests/test_loaders.py::test_curated_opensanctions_loader_flags_known_malformed_row`.

---

## 5. Repositories

Eight repositories (`app/repositories/`), each hiding SQLAlchemy from
services: `ClientRepository`, `AccountRepository`, `TransactionRepository`,
`SanctionsRepository`, `OwnershipRepository`, `ArticleRepository`,
`EvidenceRepository`, `DatasetSourceStatusRepository` (the last one
extracted from Phase 1's `validate_all.py` so validation and ingestion write
through the same status-tracking code path).

Every `upsert*` method returns `(row, created: bool)` -- callers, tests, and
loaders can always tell a create from an update. Repositories `flush()`
rather than `commit()` internally (except `AuditLogRepository`, unchanged
from Phase 1), so a loader controls its own transaction boundary and a
whole-file ingest either fully succeeds or the caller can roll it back.

---

## 6. Customer360Service

`Customer360Service.get_customer_360(client_id, ...)` assembles:

- The client's own record + accounts (always, from the database, fast).
- A shallow-transaction summary (always, from the database, fast).
- **Optionally** (three independent flags, all default `False`):
  `include_sanctions_lookup`, `include_adverse_media_lookup`,
  `include_deep_transactions` -- each fans out to every registered provider
  in that category via `ProviderExecutionService`, and the response's
  `provider_availability` list reports exactly which providers were queried
  and what happened (`SUCCESS`/`NO_RESULTS`/`NOT_CONFIGURED`/`ERROR`/etc.).

**Why opt-in, not default:** `include_sanctions_lookup=True` can take up to
~45s (dominated by the OpenSanctions provider) and `include_deep_transactions`
up to ~45s per account. Defaulting these on would make `GET /customers/{id}/360`
unacceptably slow for routine use -- "application must remain fast" (Phase 1)
extends to this endpoint. A caller who needs the deeper lookup asks for it
explicitly and accepts the cost.

**Two fields are deliberately, honestly empty for every real client:**
- `ownership_note` explains that no UBO graph is linked to any client_id --
  Phase 0 confirmed this is a real gap in the source data, not something to
  paper over.
- `sanctions_candidates` / `adverse_media_candidates` are **unconfirmed
  provider hits**, never called "matches" -- no entity-resolution scoring
  exists yet (a future phase). Verified live: querying Customer 360 for
  client_id=3 ("Phillips-Hanson") with `include_sanctions_lookup=True`
  returns `NO_RESULTS` from the curated and OFAC providers and `SUCCESS`
  (10 fuzzy candidates) from OpenSanctions -- exactly the "genuine
  false-positive surface" Phase 0 predicted, not a real match.

No AI, no scoring anywhere in this service.

---

## 7. Provider Execution Layer

`ProviderExecutionService.execute(provider, operation, *, category, timeout_seconds=None)`
is the single call path every provider goes through:

- Checks `is_configured()` first and short-circuits to `NOT_CONFIGURED`
  without spawning a thread.
- Runs the operation in a `ThreadPoolExecutor`-backed deadline; on timeout,
  calls `shutdown(wait=False)` so the **caller** returns immediately rather
  than blocking for the full hang duration (see SS8 -- this was a real bug,
  not a design assumption).
- Retries `ERROR`/`TIMEOUT`/`RATE_LIMITED` up to `max_retries` times with
  linear backoff; never retries `NOT_CONFIGURED`.
- Converts any raised exception into an `ERROR` `ProviderResult` -- nothing
  a provider does can propagate an unhandled exception past this service.

Proven with deliberately flaky/hanging/raising/unconfigured synthetic test
providers in `tests/test_provider_execution_service.py`, not mocked-away
assumptions.

---

## 8. Real Bugs Found and Fixed During This Phase

Development leaned on running every piece against the actual Phase 0 data
rather than trusting it would work, which surfaced three genuine defects
that unit tests against synthetic data alone would very likely have missed.
Full technical detail and rationale for each fix is in
`docs/ARCHITECTURE_DECISIONS.md` (ADR-006, ADR-007, ADR-008); summarized
here:

1. **SQLite query-planner misselection** (ADR-006): the shallow-transaction
   upsert lookup went from an unbounded hang (285s+ and climbing) to 41s
   once a composite unique index matching the actual query shape replaced
   two competing single-column indexes. Root-caused with raw `sqlite3`
   comparisons proving it was a SQLAlchemy/schema issue, not a filesystem or
   WAL issue.
2. **Boolean `SUM()` coercion** (ADR-007): `TransactionRepository.summary_for_client`
   silently returned Python `True` instead of an integer count (22) for
   `flagged_count`, because SQLAlchemy infers a boolean-comparison
   expression's aggregate result as `Boolean` and coerces any non-zero sum
   back to `True`. Fixed with an explicit `cast(..., Integer)`.
3. **`ThreadPoolExecutor` context-manager blocking on timeout** (ADR-008):
   the provider execution layer reported `TIMEOUT` correctly but the calling
   thread still blocked for the full hang duration, because
   `with ThreadPoolExecutor(...) as executor:` waits for the submitted task
   on `__exit__`. Fixed by managing the executor's lifecycle explicitly with
   `shutdown(wait=False)`.

---

## 9. Known Limitations

1. No entity resolution exists -- sanctions/adverse-media provider hits are
   unconfirmed candidates, not matches. Confidence scoring is a future
   phase.
2. `Tier1OfacLookupProvider` doesn't match `ofac_alt.csv` aliases yet (only
   `SDN_Name`); `ofac_add.csv` (addresses) has no consuming provider at all.
3. No persistent search index for the Tier-1 files -- every search is a
   linear stream. Acceptable at measured cost for an opt-in lookup; would
   need revisiting for a higher-traffic use case.
4. `EntityMatch`/`Evidence` remain schema-only -- nothing in Phase 2 writes
   to them (writing evidence requires investigative judgment, out of scope
   here).
5. Ingestion is synchronous over HTTP (`POST /api/v1/ingestion/load` can
   take ~43s for `all=true`) -- no task queue exists in this project by
   design (Celery/Redis explicitly out of scope). Acceptable for an
   infrequent, operator-triggered action.
6. `POST /api/v1/ingestion/load` and `/validate` accept no authentication --
   fine for a local hackathon demo, would need addressing before any
   non-local deployment.

---

## 10. API Endpoints Added

| Method & path | Purpose |
|---|---|
| `POST /api/v1/ingestion/validate` | Header/schema validation, optionally scoped to specific `source_keys`. |
| `POST /api/v1/ingestion/load` | Real ingestion: one `source_key`, or `all=true` (+ optional `include_large` to acknowledge lookup-only sources in the result list -- never bulk-loads them). |
| `GET /api/v1/customers` | Paginated client list with `sanctions_flag`/`pep_flag`/`sector_risk`/`mapped_only` filters. |
| `GET /api/v1/customers/{client_id}` | Single client by **external** client_id (the source dataset's identifier, not the internal surrogate key). |
| `GET /api/v1/customers/{client_id}/360` | Full Customer 360 profile; three opt-in query params for live provider lookups. |
| `GET /api/v1/datasets/status` | Operational ingestion-status view (distinct from Phase 1's `/api/v1/sources` catalog view). |
