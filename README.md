# Continuous KYC Autonomous Auditor

An autonomous system that continuously monitors high-risk corporate accounts,
detects meaningful risk changes, reduces false positives via entity
resolution, investigates high-risk triggers, explains risk exposure with
evidence and confidence levels, builds a risk-change timeline, drafts a
Suspicious Activity Report (SAR) for human review, and maintains a complete
audit trail.

**Core design principle:** AI/agents detect, classify, investigate,
summarize, and explain. Deterministic application logic calculates the final
risk score and enforces workflow rules. Humans make the final compliance
decision. No LLM is ever the sole authority for a numerical risk score.

## Project status

- **Phase 0 -- Dataset audit:** complete. See `docs/phase-0-dataset-audit.md`,
  `docs/data-dictionary.md`, `docs/system-design-phase-0.md`.
- **Phase 1 -- Backend foundation:** complete. See `docs/phase-1-foundation.md`,
  `backend/README.md`.
- **Phase 2 -- Ingestion, normalization, Customer 360:** complete. See
  `docs/phase-2-ingestion.md`.
- **Phase 3 -- Entity resolution & evidence engine:** complete. See
  `docs/phase-3-entity-resolution.md`.
- **Phase 4 -- Continuous monitoring & explainable risk intelligence:** complete.
  See `docs/phase-4-risk-intelligence.md`.
- **Phase 5 -- Autonomous investigation engine (first LLM):** complete. See
  `docs/phase-5-investigation-agent.md`.
- **Phase 6 -- Enterprise case management (timeline, human review, Draft SAR,
  audit):** complete. See `docs/phase-6-case-management.md`.
- **Phase 7 -- Enterprise frontend (React/Vite/TS):** complete. See
  `docs/phase-7-frontend.md` and `frontend/`.

Every major design decision across all phases is recorded in
`docs/ARCHITECTURE_DECISIONS.md`.

## Repository layout

```
data/               Phase 0 datasets (KYC profiles, transactions, sanctions,
                     adverse-media and UBO fixtures) -- see docs/data-dictionary.md
docs/                Phase-by-phase design documentation
frontend/            React + Vite + TypeScript UI (Phase 7) -- see docs/phase-7-frontend.md
scripts/             Read-only dataset profiling utility (Phase 0)
backend/             FastAPI backend (Phase 1+) -- see backend/README.md
```

## Quick start (backend)

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then visit http://localhost:8000/docs, or:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/api/v1/sources
curl http://localhost:8000/api/v1/providers

# Ingest the real Phase 0 datasets (~43s), then browse the result
curl -X POST http://localhost:8000/api/v1/ingestion/load -H 'Content-Type: application/json' -d '{"all": true}'
curl http://localhost:8000/api/v1/datasets/status
curl http://localhost:8000/api/v1/customers/3/360
```

See `backend/README.md` for full setup, testing, and dataset-validation
instructions.

## Architecture at a glance

The system is designed as a hybrid intelligence platform, not a fixed
pipeline over one dataset:

```
INTERNAL DATA (Phase 0 KYC profiles/transactions)     [ingested -> SQLite]
        +
LOCAL REFERENCE DATA (OFAC/OpenSanctions, curated fixtures)  [streamed, never bulk-loaded]
        +
LIVE EXTERNAL APIs (sanctions/adverse-media/corporate-registry -- future)
        |
        v
   NORMALIZATION                          <- Phase 2
        |
        v
 CUSTOMER 360 (normalized profile)        <- Phase 2
        |
        v
 ENTITY RESOLUTION                        <- Phase 3
        |
        v
     EVIDENCE                             <- Phase 3
        |
        v
   RISK ENGINE (deterministic)            <- Phase 4
        |
        v
AUTONOMOUS INVESTIGATION (LLM-assisted, evidence-grounded)   <- Phase 5
        |
        v
  HUMAN REVIEW + CASE / TIMELINE / DRAFT SAR   <- Phase 6
```

The LLM enters at exactly one point, and only at that point. It explains the
score; it cannot compute one. Everything above it is deterministic, everything
below it is a human, and the boundary is enforced by tests that read the code's
AST rather than by convention -- see `docs/phase-5-investigation-agent.md` SS1.

The provided Phase 0 datasets bootstrap and evaluate the system -- they do
not define or limit its architecture. Every data source, whether an ingested
CSV or a future live API, reaches the rest of the system through the same
normalized contracts. See `docs/phase-1-foundation.md` for the provider/
adapter design that makes this possible, `docs/phase-2-ingestion.md` for the
ingestion/large-dataset strategy, and `docs/phase-0-dataset-audit.md` for
what the datasets do and don't support.

## Documentation index

| Phase | Document |
|---|---|
| all | `docs/ARCHITECTURE_DECISIONS.md` -- ADR log: every major design choice, why, and what it costs |
| all | `docs/PHASE_LOG.md` -- running phase-by-phase log |
| 0 | `docs/phase-0-dataset-audit.md` -- full dataset audit |
| 0 | `docs/data-dictionary.md` -- column-level reference |
| 0 | `docs/system-design-phase-0.md` -- proposed architecture |
| 1 | `docs/phase-1-foundation.md` -- backend foundation writeup |
| 2 | `docs/phase-2-ingestion.md` -- ingestion, normalization, Customer 360 |
| 3 | `docs/phase-3-entity-resolution.md` -- matching, confidence, evidence |
| 4 | `docs/phase-4-risk-intelligence.md` -- monitoring, risk engine, alerts |
| 5 | `docs/phase-5-investigation-agent.md` -- the investigation agent, grounding, the LLM boundary |
| 6 | `docs/phase-6-case-management.md` -- case workspace, timeline, review workflow, Draft SAR, audit |
| 7 | `docs/phase-7-frontend.md` -- React frontend: pages, components, API integration, limitations |
| - | `CLAUDE.md` -- project context + hard-won traps |
| - | `backend/README.md` -- backend setup/usage |
