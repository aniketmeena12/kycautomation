# Phase 3 -- Entity Resolution & Evidence Engine

**Continuous KYC Autonomous Auditor**
**Status:** complete. Builds the generic matching + evidence layer on Phase 2's
data layer. No risk scoring, timeline, investigation, agents, SAR, or LLMs --
those remain future phases.

---

## 1. Why this phase exists

`docs/phase-0-dataset-audit.md` SS6 established, before any code existed, that
fuzzy-matching names against the real sanctions lists produces **genuine false
positives** in this data. Searching for the UBO showcase's "Mohammad Al-Rashid"
surfaces:

| Candidate | Why it's a false positive |
|---|---|
| `AL-RASHID TRUST` | A trust, not a person |
| `AL-RASHIDI, NAWAF AHMAD ALWAN` | Different given name, different nationality |

Nothing about the **names** rules those out. What rules them out is entity
type, nationality, and DOB. Phase 3 is that mechanism, built generically.

The engine reproduces Phase 0's prediction exactly, measured:

```
TRUE HIT   AL-RASHID, Mohammad             89.2 -> HIGH_CONFIDENCE   (no conflicts)
FP         AL-RASHID TRUST                 29.4 -> AUTO_REJECTED     (entity_type conflict, -35)
FP         AL-RASHIDI, NAWAF AHMAD ALWAN   29.8 -> AUTO_REJECTED     (nationality conflict + name floor)
UNRELATED  Nordvale Dairy Cooperative        0.0 -> AUTO_REJECTED
```

Regression-tested in `tests/test_resolution_pipeline.py`.

---

## 2. Architecture

```
                    ┌──────────────────────────────────────────┐
  Client ─┐         │  adapters.py                             │
  UBO     ├────────▶│  everything -> ResolutionSubject         │
  Sanctions│        │  (the ONLY module that knows entity types)│
  Provider─┘        └──────────────────┬───────────────────────┘
                                       ▼
                    ┌──────────────────────────────────────────┐
                    │ 1. candidates.py    blocking + providers │
                    │ 2. pipeline._exact_match_ref             │
                    │ 3. scorers/name.py       (fuzzy)         │
                    │ 4. scorers/attributes.py (attribute)     │
                    │ 5. scorers/attributes.py (context)       │
                    │ 6. confidence.py    (deterministic)      │
                    │ 7. confidence.explain()                  │
                    └──────────────────┬───────────────────────┘
                                       ▼
                          EntityResolutionResult
                                       │
              ┌────────────────────────┴────────────────────┐
              ▼                                             ▼
   EntityResolutionService                          (caller may just read it --
   persists EntityMatch + Evidence                   the pipeline never writes)
```

**The pipeline is pure computation and never writes.** Persisting is the
service layer's job. That split is what makes `resolve_pair()` usable with no
database, no providers, and no ingestion -- see
`POST /entity-resolution/resolve-pair`.

### Genericity

`ResolutionSubject` is the single shape everything reduces to. `adapters.py`
is the only module that knows what a Client or a SanctionsEntity looks like;
no scorer, no confidence rule, and no pipeline stage can branch on entity
source or on any specific name. Guarded by
`tests/test_resolution_scorers.py::test_no_scorer_hardcodes_any_entity_name`
and demonstrated by `resolve-pair` scoring entities the system has never seen.

---

## 3. Matching features

Nine scorers, each independently testable, each returning a `ScorerResult`
(score + reason + signed confidence impact) -- **never a bare boolean**, because
a boolean discards exactly what the confidence and explainability layers need.

| Scorer | Compares | Conflict when |
|---|---|---|
| `name` | Primary names (RapidFuzz, metric by type) | -- (floor rejects instead) |
| `alias` | All names x all aliases, both sides | -- |
| `country` | ISO-2 sets | Both populated, zero overlap |
| `nationality` | ISO-2 sets | Both populated, zero overlap |
| `entity_type` | person/organization/vessel/aircraft | Both known and different |
| `dob` | Full date, else year | Different **years** |
| `identifier` | Normalized registry/passport ids | Both populated, none shared |
| `ownership` | Shared related-entity refs | Both populated, none shared |
| `organization` | Employer/org names (fuzzy) | Below match threshold |

### Three states, not two

Every scorer distinguishes **agreement / contradiction / absent**:

- `applicable=False` (absent) contributes nothing and costs nothing.
- `is_conflict=True` (contradiction) actively subtracts.

Conflating these would reject nearly every true match in this dataset, because
most Tier-1 OFAC rows have empty `Remarks` and therefore no DOB at all
(`docs/data-dictionary.md`). **Absence of evidence is not evidence of absence** --
Phase 0 SS6G flagged this as a requirement before implementation.

### RapidFuzz metric selection

| Entity type | Metric | Why |
|---|---|---|
| person | `token_sort_ratio` on token-sorted key | OFAC writes `AL-RASHID, Mohammad`; OpenSanctions writes `Mohammad Al-Rashid`. Word order is noise. |
| organization | `token_set_ratio` on suffix-stripped key | Company names differ by extra/missing tokens (`Greenfield Technologies` vs `... Pte Ltd`). |
| unknown | max of both | Never penalize an entity for our not knowing its type. |

`ratio` and `partial_ratio` are computed alongside, reported in the reason
string, and used as a guard: `token_set_ratio("aegean", "aegean ventures
cyprus") == 100` -- a perfect score for a mere prefix. `_blend()` caps the
primary metric against plain `ratio`, a deliberate precision-over-recall choice
for a compliance context. Regression-tested.

---

## 4. Normalization

`app/resolution/normalization.py` is **separate from** `app/ingestion/normalizers.py`
by design:

- **Ingestion normalization** preserves identity. Storing `AL-RASHID, Mohammad`
  as `al rashid mohammad` would destroy real information.
- **Matching normalization** aggressively strips what is noise *to a matcher*:
  legal suffixes, punctuation, case, accents.

Both are needed; originals are always preserved (these are pure functions that
return new strings and never write).

Company suffixes are a **linguistic** list (`ltd`, `gmbh`, `llc`, ...) containing
zero entity names, so it cannot bias toward any record. Stripping never returns
an empty key -- a company named "Holdings Ltd" keeps its un-stripped form,
because an empty comparison key would match everything.

Entity types are mapped from the project's two incompatible vocabularies
(OFAC's `individual/vessel/aircraft/blank`, OpenSanctions' `Person/Company/
LegalEntity/...`) onto a small shared set for **compatibility checks only** --
the raw stored values are never rewritten.

---

## 5. Candidate generation

Two sources with sharply different costs:

1. **Local DB (default, fast).** Blocking: an indexed SQL `LIKE` on significant
   name tokens narrows the space *before* any fuzzy scoring. Legal-form tokens
   are excluded from blocking -- blocking on `ltd` would match most of a company
   list and defeat the purpose. Reaches entities via the **alias join**, so
   `M. Rashid` retrieves the row whose primary name is `AL-RASHID, Mohammad`.
2. **Providers (opt-in).** `EXPENSIVE_PROVIDERS` (the Tier-1 OpenSanctions
   stream: ~40-45s over 1.3M rows, measured in `docs/phase-2-ingestion.md` SS3)
   is **never queried unless explicitly requested**. This is the Phase 3 SS14
   rule, enforced by test, not convention.

Measured: resolving the real UBO person against the ingested curated fixture
examines **1 candidate out of 17 rows in 0.01s**.

---

## 6. Confidence engine

Deterministic arithmetic over scorer outputs and **externally-configured**
weights. No ML, no LLM, no hidden heuristics.

```
earned   = SUM(score_i x positive_weight_i)   over APPLICABLE scorers
possible = SUM(positive_weight_i)             over APPLICABLE scorers
base     = (earned / possible) x 100
penalty  = SUM(conflict_penalty_i)            over CONFLICTING scorers
final    = clamp(base - penalty, 0, 100)
```

Two deliberate properties:

1. **`possible` counts only applicable scorers.** A pair lacking DOB data isn't
   punished -- the denominator shrinks. Dividing by the full total would make
   every match against sparse Tier-1 rows look weak regardless of agreement.
2. **A conflict is penalized twice** -- it forfeits its positive weight *and*
   subtracts a penalty. A contradiction is not "no evidence for", it is evidence
   against, and it must be able to sink a perfect name match. This is precisely
   what makes `AL-RASHID TRUST` (name 77/100) resolve to AUTO_REJECTED.

**Name floor:** a pair below `thresholds.name_floor` is rejected outright.
Two entities sharing a country and a type are not a match if the names don't
match -- there's no candidate identity to corroborate in the first place.

### Weights are configuration, not code

`backend/config/resolution_weights.json`, loaded and **strictly validated** by
`app/resolution/config.py` (missing key or negative weight raises at load --
a compliance system that quietly mis-weights evidence is worse than one that
refuses to start). No fallback to in-code defaults, which would defeat the
externalization. Proven data-driven by
`test_confidence_respects_custom_weights_from_config`, which changes the file
and gets a different answer from identical inputs.

**These weights are expert-set, not statistically calibrated -- and they say
so in the file.** Phase 0 SS14 established this dataset cannot support real
calibration (client names resolve to nothing authoritative; only 60 clients
carry behavioural ground truth). Presenting them as fitted would be a lie.

### Statuses

The engine reaches **only** CANDIDATE / POSSIBLE / HIGH_CONFIDENCE /
AUTO_REJECTED. It **never** produces CONFIRMED or HUMAN_REVIEWED -- those are
reserved for a human in a later phase (Phase 3 brief SS9). Enforced two ways:
an exhaustive test over the reachable status space, and a runtime
`_assert_machine_status` guard in the service that raises rather than persists.

`AUTO_REJECTED` is the brief's "Rejected" state, kept under its Phase 1 name
(the machine rejected it, not a person) rather than renamed, so existing rows
and queries stay valid.

---

## 7. Evidence engine

`EvidenceService` is the single writer for Evidence rows. All six kinds the
brief requires exist as methods: sanctions hit, news article, transaction,
ownership graph, provider response, manual. They funnel through one `_create`,
so provenance, size bounds, and the structured/prose split are enforced once.

Every row carries: type, source, provenance, timestamp, prose summary,
**structured facts (JSON)**, linked entity, linked client, confidence.

`confidence` is **copied** from the resolution result, never recomputed -- the
confidence engine is the single authority on that number. Two components
computing it independently is how they drift apart.

### The evidence graph

The graph is these FKs, not a separate structure:

```
Client --client_id--> Evidence --entity_match_id--> EntityMatch
                          |                              |
                          |                candidate_sanctions_entity_id (DB candidate)
                          |                or candidate_provider + external_id (provider candidate)
                          +-- source_dataset / provider_name --> Source
```

Multiple Evidence rows per entity is the normal case (tested).

**Rejected matches produce an EntityMatch but no Evidence.** A compliance
system must show what it considered and dismissed -- that's the audit story for
a false positive -- but "we looked and it wasn't him" is not a fact supporting
a later risk decision.

### Model changes (additive)

Phase 1 created `EntityMatch` as an empty contract; Phase 3 populates and
extends it. `candidate_sanctions_entity_id` became **nullable**: a
streaming-provider candidate has no local row (Phase 2 never bulk-loads those
files), so forcing an FK would mean bulk-loading 488 MB purely to satisfy a
constraint. Provider candidates use `candidate_provider` +
`candidate_external_id` instead. `Evidence` gained `structured_facts` and
`entity_match_id`. No column was removed or repurposed.

---

## 8. Explainability

Nothing is opaque. Every result carries positive factors, negative factors,
not-applicable factors, and a summary:

```
Confidence 89/100 -> HIGH_CONFIDENCE. Earned 83 of 93 applicable weight
(base 89), penalties -0. Matched: ['name','alias','nationality','entity_type','dob'].
Conflicts: none. Not comparable: ['country','identifier','ownership','organization'].
```

Persisted matches store the full explanation as JSON, so a match explains
itself later **without re-running the pipeline**.

---

## 9. APIs

| Method & path | Purpose |
|---|---|
| `POST /api/v1/entity-resolution/resolve-pair` | Score two supplied entities. No DB, no providers, nothing persisted. |
| `POST /api/v1/entity-resolution/resolve` | Resolve one subject (by `subject`, `client_id`, or `ownership_entity_id`) against generated candidates. |
| `POST /api/v1/entity-resolution/batch` | Up to 50 subjects, sequential. |
| `GET /api/v1/entity-resolution/matches` | List by `subject_ref` or `status`. |
| `GET /api/v1/entity-resolution/{id}` | One persisted match. |
| `GET /api/v1/evidence/{entity_match_id}` | Evidence attached to a match. |
| `GET /api/v1/evidence/client/{client_id}` | Evidence linked to a client. |

`allow_expensive_providers` defaults to **False** everywhere (ADR-009 posture).
Batch is sequential, not concurrent -- fanning provider I/O out against a
single-writer SQLite session would fight ADR-001.

Evidence endpoints are **read-only**: an endpoint letting a caller post
arbitrary "facts" would undermine the entire traceability story.

---

## 10. Performance

| Operation | Measured |
|---|---|
| Resolve real UBO person vs. ingested curated fixture | 0.01s, 1 candidate of 17 rows |
| `resolve-pair` (pure) | sub-millisecond |
| Full Phase 3 test suite (101 tests) | ~8s |
| Full suite (215 tests, all phases) | 115s |

No 1.3M-row scan occurs on any default path.

---

## 11. Known limitations

1. **Blocking is a real recall trade.** An entity sharing no significant name
   token with the query is not retrieved from the local DB, however high its
   fuzzy score would be (`Mohamed` vs `Muhammad` block differently). Accepted
   because the alternative is loading the whole table per query. Stated, not
   hidden.
2. **Clients can rarely exceed CANDIDATE** against authoritative lists -- the
   client master has no DOB, nationality, or identifiers to corroborate with
   (`docs/data-dictionary.md`). This is Phase 0 SS3's finding reproduced, not a
   defect. The engine reports it honestly rather than manufacturing a hit.
3. **Weights are expert-set, not calibrated** (SS6).
4. **`country` vs `nationality` are not disambiguated** for DB-sourced sanctions
   rows. The sources conflate them; rather than guess, the adapter populates
   `country` and leaves `nationality` empty, so the nationality scorer reports
   not-applicable instead of risking a false conflict.
5. **`Tier1OfacLookupProvider` still doesn't match aliases** (Phase 2
   limitation, unchanged) -- so provider-sourced OFAC candidates carry no
   aliases, weakening the alias scorer for that path. The local DB path is
   unaffected.
6. **Identifier conflicts are weak by design** -- the data doesn't label
   identifier types, so "these lists don't intersect" is only weak evidence
   against.
7. **No transitive resolution / clustering.** Pairwise only; A~B and B~C does
   not infer A~C.
8. **Batch is sequential**, capped at 50.

---

## 12. Boundary: what Phase 3 did NOT build

No risk scoring, no timeline, no investigation workflow, no agents, no SAR,
no LLM calls, no frontend. `RiskEvent`/`RiskScoreSnapshot`/`Investigation`/
`SARDraft` remain schema-only, exactly as Phase 1 left them.

Phase 3 produces **evidence and confidences**. Turning those into an
authoritative risk score is Phase 4's job, and it must remain deterministic
application logic -- the project's core design principle.
