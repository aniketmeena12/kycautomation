# Project Metrics

All figures **measured**, not estimated. Generated at the Phase 8 review.

## Code

| | Files | Lines |
|---|---:|---:|
| Backend (`backend/app`) | 144 | 17,911 |
| Backend tests (`backend/tests`) | 42 | 7,263 |
| Frontend (`frontend/src`) | 20 | 5,006 |
| **Total code** | **206** | **30,180** |
| Documentation (`docs/`) | 13 | 5,672 |

Test-to-code ratio (backend): **0.41 lines of test per line of app code**.

## Tests

| Suite | Count | Runtime |
|---|---:|---|
| Backend (pytest) | **476** | ~4m (dominated by two deliberately-slow tests that read the real 951 MB / 488 MB files once each) |
| Frontend (vitest) | **21** | ~6s |
| **Total** | **497** | |

**Coverage is not reported.** `pytest-cov` is not installed and adding it at the
final review would produce a number nobody acted on. What *is* meaningful and
verified: the AST-level boundary tests (the risk engine imports no LLM SDK; the
agent imports no DB/writer), the live prompt-injection acceptance test, and the
real-dataset regression pinning client 3 at exactly 53.0. Those catch the things
a coverage percentage would not.

## API

| | Count |
|---|---:|
| Endpoints | **40** |
| Consumed by the frontend | 20 |
| Route modules | 13 |
| Tags | 12 |

## Data model

| | Count |
|---|---:|
| ORM entities | 20 |
| Database tables | 25 |
| Repositories | 13 |
| Services | 8 |

## Datasets (Phase 0)

| | |
|---|---|
| Registered sources | 16 |
| Files on disk | 979 |
| Total size | **1.70 GB** |
| Largest | SAML-D 951 MB (9.5M rows), OpenSanctions 488 MB (1.3M rows) |
| Never bulk-loaded | SAML-D, OpenSanctions, OFAC ×3 (`LOOKUP_ONLY`, streamed) |
| SQLite after full ingest | ~18 MB against 1.6 GB of source — proof the lookup-only strategy holds |

## Providers

| Kind | Count | Detail |
|---|---:|---|
| Data providers | 8 | 2 local sanctions, 1 adverse media, 2 Tier-1 streaming, 1 SAML-D, 1 pending-API placeholder |
| **LLM providers** | **2** | `anthropic`, `groq` — both real, behind one Protocol |
| Genuinely unimplemented | 3 | news / sanctions / corporate-registry external APIs: **zero network calls**, honestly report `NOT_CONFIGURED` |

## Monitoring capability

| | |
|---|---|
| Risk factors | 12, **config-driven** (`config/risk_factors.json`) — a new factor needs no code change |
| Signal collectors | 3 (internal, resolution, provider) |
| Alert triggers | 5 |
| Entity-resolution scorers | 5, config-weighted (`config/resolution_weights.json`) |
| Case states | 5, validated state machine |
| Timeline sources | 8 tables, 10 entry types |
| Review actions | 12 (8 Phase 6 + 4 legacy) — **APPROVE/REJECT of a client are absent by design** |

## Decisions

**37 ADRs** across 8 phases. Every phase added entries; none were retrofitted.

## Provenance

| Tier | Meaning |
|---|---|
| `TIER_1_AUTHORITATIVE` | Real OFAC / OpenSanctions |
| `TIER_2_CURATED_DEMO` | 18-entity curated fixture — **never presentable as authoritative** |
| `INTERNAL` | This platform's own records |
| `EXTERNAL_LIVE` | Retrieved at runtime |

## The number that matters most

**0/2000.** Client names match **zero** entries in the authoritative sanctions
lists. Measured in Phase 0, reproduced honestly at every layer since. The system
never manufactures a match to look impressive.
