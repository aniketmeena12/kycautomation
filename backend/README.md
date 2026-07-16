# Continuous KYC Autonomous Auditor -- Backend

FastAPI + SQLAlchemy + SQLite backend: dataset source registry, provider
architecture, audit infrastructure (Phase 1), plus real ingestion,
normalization, repositories, and Customer 360 (Phase 2). See
`docs/phase-1-foundation.md` and `docs/phase-2-ingestion.md` (project root)
for the full design writeups, and `docs/ARCHITECTURE_DECISIONS.md` for the
reasoning behind each major choice.

## Setup

From `backend/`:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Bash: source .venv/bin/activate
pip install -r requirements.txt
```

No `.env` file is required to run locally -- every setting has a working
default (see `app/core/config.py`). Copy `.env.example` to `.env` only if you
need to override something.

## Initialize the database

Tables are also created automatically on app startup, but you can do it
explicitly:

```bash
python init_db.py
```

This only ever creates missing tables (`Base.metadata.create_all`) -- it
never drops or truncates anything.

## Run the API

```bash
uvicorn app.main:app --reload
```

Then:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
curl http://localhost:8000/api/v1/sources
curl http://localhost:8000/api/v1/providers
```

Interactive API docs: http://localhost:8000/docs

## Validate dataset sources (optional, on-demand)

Runs a header/schema smoke check against every registered source (samples a
few rows -- never a full read of a large file) and records the result:

```bash
python -m app.ingestion.validate_all
# or: curl -X POST localhost:8000/api/v1/ingestion/validate -H 'Content-Type: application/json' -d '{}'
```

## Ingest the datasets

Loads the 10 small/curated Phase 0 sources into SQLite (~43s -- dominated by
the 50,000-row transaction file). Idempotent: re-running upserts, never
duplicates.

```bash
curl -X POST localhost:8000/api/v1/ingestion/load \
     -H 'Content-Type: application/json' -d '{"all": true}'

# Or one source at a time:
curl -X POST localhost:8000/api/v1/ingestion/load \
     -H 'Content-Type: application/json' -d '{"source_key": "clients"}'

curl localhost:8000/api/v1/datasets/status
```

SAML-D (951 MB) and the Tier-1 OFAC/OpenSanctions files are **never**
bulk-loaded -- they're registered `LOOKUP_ONLY` and served live by streaming
providers. Requesting one via `/ingestion/load` honestly returns
`SKIPPED_LOOKUP_ONLY` rather than silently doing nothing.

## Browse Customer 360

```bash
curl localhost:8000/api/v1/customers?limit=5
curl localhost:8000/api/v1/customers?mapped_only=true   # the 60 clients with real account history
curl localhost:8000/api/v1/customers/3                  # by external client_id
curl localhost:8000/api/v1/customers/3/360              # fast path (milliseconds)

# Opt into live provider lookups (slow -- see docs/phase-2-ingestion.md SS3 for measured costs)
curl 'localhost:8000/api/v1/customers/3/360?include_sanctions_lookup=true'
curl 'localhost:8000/api/v1/customers/3/360?include_deep_transactions=true'
```

## Run tests

```bash
pytest
```

Tests run against a temporary SQLite file (never `backend/data/continuous_kyc.db`)
and never modify anything under the project's `data/` directory. The suite
takes ~3 minutes, most of it in two deliberately-slow tests that exercise the
real 951 MB / 488 MB files once each rather than repeatedly.

## Where things live

```
app/
  main.py            FastAPI app, lifespan, router registration
  core/               config.py (Settings), database.py (engine/session/init_db), enums.py
  models/             SQLAlchemy ORM models (19 entities across 12 files)
  schemas/            Pydantic request/response contracts
  providers/          Pluggable data providers -- curated fixtures, Tier-1 streaming
                       lookups, SAML-D lookup, and future external APIs
  registry/           Static dataset source registry (app/registry/sources.py)
  ingestion/          normalizers.py, validators, loaders/, commands.py
  repositories/       Persistence layer (8 repositories)
  services/           audit, provider execution (timeout/retry), Customer 360
  api/routes/         health, sources, providers, ingestion, customers, datasets
tests/                 pytest suite (114 tests as of Phase 2)
init_db.py            Standalone DB init script
```

## Source-tier vocabulary

Every sanctions/watchlist record and every provider is tagged with a
`SourceTier`: `TIER_1_AUTHORITATIVE` (real OFAC/OpenSanctions data),
`TIER_2_CURATED_DEMO` (the deliberately-curated fixture set), `INTERNAL`
(this project's own KYC data), or `EXTERNAL_LIVE` (a future live API
result). Nothing in this codebase merges these without the tag traveling
with the record -- see `docs/phase-1-foundation.md` for why.
