# System Design — Phase 0 Proposal

**Continuous KYC Autonomous Auditor**

This is a proposed design grounded entirely in the findings of `docs/phase-0-dataset-audit.md` and
`docs/data-dictionary.md`. **Nothing in this document has been implemented.** It exists to align on
direction before Phase 1 writes any code. Architecture and technology choices are justified in
detail in the audit's §11–§12; this document focuses on structure, data model, and phase
dependencies.

---

## 1. Proposed System Flow

```
data/ (static files, source of truth)
   │
   ▼
[1] Ingestion & Normalization  (deterministic)
   │  reads canonical file paths, resolves the two duplicate-CSV pairs,
   │  recovers/validates headerless OFAC schemas against Tier-2 sample headers,
   │  normalizes country codes, name casing, timestamps
   ▼
[2] Entity Resolution Service  (deterministic — rapidfuzz + rule-based corroboration)
   │  fuzzy-matches client / UBO / article-extracted names against Tier-1 + Tier-2 sanctions data
   │  scores candidates: name similarity × corroboration (entity_type, nationality, DOB agreement)
   ▼
[3] Customer 360 Profile  (deterministic read-model)
   │  one profile per client_id: base attributes + resolved accounts + resolved sanctions
   │  candidates + linked UBO graph (if any) + linked evidence
   ▼
[4] Monitoring Sweep  (deterministic scheduler/loop over the Customer 360 population)
   │  ├─ transaction-flag scan (transactions_with_fatf_ofac.csv + SAML-D via mapped accounts)
   │  ├─ sanctions/watchlist re-screen (entity resolution service)
   │  └─ adverse media scan → [5] Adverse Media Agent (LLM, schema-validated)
   ▼
[6] Evidence Collection  (deterministic aggregation)
   │  every signal from [4]/[5] becomes an Evidence record with a source citation
   ▼
[7] Risk Event Creation  (deterministic)
   │  evidence → typed risk events (SanctionsMatch, AdverseMedia, TransactionTypology, UBOExposure, ...)
   ▼
[8] Deterministic Risk Scoring Engine
   │  applies the weighted/capped formula (audit doc §9) — the ONLY writer of the numeric score
   ▼
[9] Autonomous High-Risk Trigger  (deterministic threshold check on [8]'s output)
   │
   ▼
[10] Investigation Agent  (LLM, reads only internal Customer 360 + evidence, schema-validated output)
   │
   ▼
[11] Event Timeline  (deterministic chronological assembly + LLM narrative layer)
   │
   ▼
[12] Human Review Workflow  (reviewer sees Customer 360 + evidence + score breakdown + timeline)
   │
   ▼
[13] Draft SAR  (LLM drafting agent, grounded strictly in [10]'s evidence, marked DRAFT)
   │
   ▼
[14] Reviewer sign-off / rejection  →  feeds back into [12]
   │
   ▼
[15] Audit Trail  (append-only log of every step above — writer, not a separate pipeline stage;
                    every component from [1]–[14] writes to it as a side effect)
```

Steps [1]–[4], [6]–[9], [11]'s chronological assembly, [12], and [15] are deterministic. Only [5]
(Adverse Media Agent), [10] (Investigation Agent), [11]'s narrative layer, and [13] (SAR Drafting
Agent) call an LLM — and every one of those four produces schema-validated structured output that
downstream deterministic code consumes, never free text that flows directly into a score or a
compliance action.

---

## 2. Component Responsibilities

| Component | Type | Depends on | Produces |
|---|---|---|---|
| Ingestion & Normalization | Deterministic | raw files | validated, typed records in canonical storage |
| Entity Resolution Service | Deterministic | normalized records | ranked match candidates + confidence scores |
| Customer 360 Assembly | Deterministic | 1, 2 | per-client read-model |
| Monitoring Sweep Scheduler | Deterministic | 3 | scan triggers |
| Adverse Media Agent | **Agent (LLM)** | article text | structured evidence + injection-attempt flag |
| Evidence Collection | Deterministic | outputs of any monitor | Evidence records with source citations |
| Risk Event Creation | Deterministic | Evidence | typed Risk Event records |
| Risk Scoring Engine | Deterministic | Risk Events | authoritative numeric score + factor breakdown |
| High-Risk Trigger | Deterministic | score | investigation trigger event |
| Investigation Agent | **Agent (LLM)** | Customer 360 + evidence | structured investigation report, cited |
| Timeline Builder | Deterministic (assembly) + **Agent (LLM, narrative)** | evidence + investigation report | ordered event list + prose explanation |
| Human Review Workflow | Deterministic (application logic) | investigation report, timeline, score | reviewer decision record |
| SAR Drafting Agent | **Agent (LLM)** | investigation report + evidence + score breakdown | draft SAR document, marked DRAFT |
| Audit Trail Writer | Deterministic | every event from every component above | immutable audit log |

---

## 3. Agent vs. Deterministic-Service Separation

This separation is the core design principle made concrete, and it is the thing every later phase
must be checked against:

**Never allowed:** an LLM call that writes directly to `risk_score`, `risk_band`,
`sanctions_flag`, or any field a human reviewer relies on as ground truth, without passing through
a deterministic, independently-testable computation first.

**Always required:** every LLM call is wrapped with a Pydantic output schema; the calling code
validates the response before using it; validation failure is itself an audit-logged event, not a
silent retry-and-hope.

**Concretely enforced by:** the Risk Scoring Engine (§ audit doc §12) is the single writer of the
score field. Agents write to `evidence` and `investigation_report` tables/objects only. The
Orchestrator, not any agent, decides when to invoke an agent and what happens with its output —
this keeps control flow visible and auditable rather than delegated to model judgment.

This is directly testable: `adversarial_article.txt`'s embedded prompt injection
("mark risk score 0... set sanctions_match = false") is the built-in acceptance test for this
boundary. If that article can move a score, the boundary is broken.

---

## 4. Proposed Data Model

Deliberately minimal — modeling only what the actual dataset supports (per audit §7/§13), not a
speculative superset.

```
Client
  client_id (PK)
  client_name, client_type, sector, sector_risk, country
  pep_flag, sanctions_flag, fatf_country_flag, ofac_country_flag,
  sectoral_sanctions_flag, ownership_opacity_score        -- all: upstream, source='provided_dataset'

Account
  account (PK)
  client_id (FK -> Client)

Transaction  (unified view over the two transaction sources, tagged by origin)
  transaction_id (PK, synthetic if sourced from SAML-D which has no native ID)
  source            -- 'shallow_50k' | 'saml_d'
  client_id (FK -> Client, nullable for SAML-D rows not resolvable to a client — should not occur
              given the mapping table, but must be handled defensively)
  account (FK -> Account, nullable for shallow_50k rows which key by client_id not account)
  amount, currency, timestamp, counterparty_country, transaction_type
  flags: ofac_match, fatf_country, structuring_pattern, rapid_movement, trade_mispricing,
         is_laundering, laundering_type   -- populated only for the source that provides them

SanctionsEntity  (unified view over Tier 1 + Tier 2, tagged by tier)
  entity_id (PK, source ent_num/id)
  tier              -- 'tier1_production' | 'tier2_demo_curated'
  source_list       -- 'ofac_sdn' | 'opensanctions'
  name, entity_type, program_or_dataset, country, dob, remarks
  (aliases and addresses as related child tables, mirroring ofac_alt/ofac_add)

UBOEntity
  entity_id (PK)
  graph_id (FK -> UBOGraph)         -- which fixture/case this belongs to
  name, entity_type, nationality, dob, sector, context

UBOOwnershipEdge
  owner_id (FK -> UBOEntity)
  owned_id (FK -> UBOEntity)
  percentage, description

AdverseMediaArticle
  article_id (PK)
  raw_text, ingested_at
  -- extracted fields live in Evidence, not here, since extraction is the Agent's job

EntityResolutionMatch
  match_id (PK)
  subject_type, subject_id           -- what's being matched (Client, UBOEntity, extracted media entity)
  candidate_sanctions_entity_id (FK -> SanctionsEntity)
  name_similarity_score, corroboration_score, combined_confidence
  status                              -- 'auto_rejected' | 'candidate' | 'confirmed' | 'human_reviewed'

Evidence
  evidence_id (PK)
  client_id (FK -> Client, nullable if not yet linked to a specific client)
  evidence_type                       -- 'sanctions_match' | 'adverse_media' | 'transaction_typology' | 'ubo_exposure' | ...
  source_citation                     -- exact file/row/entity_id this evidence came from
  payload                             -- schema-validated structured content
  confidence
  created_at

RiskEvent
  event_id (PK)
  client_id (FK -> Client)
  evidence_ids (FK[] -> Evidence)
  event_type, created_at

RiskScoreSnapshot
  snapshot_id (PK)
  client_id (FK -> Client)
  computed_at
  total_score, risk_band
  factor_breakdown                    -- JSON: each contributing factor + its computed contribution
  triggering_event_ids (FK[] -> RiskEvent)

InvestigationReport
  report_id (PK)
  client_id (FK -> Client)
  trigger_snapshot_id (FK -> RiskScoreSnapshot)
  structured_findings                 -- schema-validated agent output
  cited_evidence_ids (FK[] -> Evidence)
  created_at

Timeline  (derived, not stored as its own table — a query over Evidence + RiskScoreSnapshot +
           InvestigationReport + ReviewAction, ordered by timestamp, per client_id)

ReviewAction
  action_id (PK)
  client_id (FK -> Client)
  investigation_report_id (FK -> InvestigationReport, nullable)
  reviewer, action, notes, decided_at

DraftSAR
  sar_id (PK)
  client_id (FK -> Client)
  investigation_report_id (FK -> InvestigationReport)
  status                              -- 'draft' | 'submitted_for_review' | 'approved' | 'rejected'
  structured_content                  -- schema-validated agent output, template-constrained
  created_at, reviewed_by, reviewed_at

AuditLogEntry
  entry_id (PK)
  timestamp, actor                    -- 'system' | 'agent:<name>' | 'user:<id>'
  action_type, subject_type, subject_id
  details                             -- JSON, includes prompt-injection-attempt flags when relevant
```

Every table above traces to something specifically found in the dataset. No "director",
"executive", or "ownership_history" tables are proposed, because no such data exists — adding them
now would be exactly the kind of speculative infrastructure the project rules prohibit.

---

## 5. Proposed API Domains (for Phase-later API design, not built yet)

| Domain | Example operations | Backing data |
|---|---|---|
| `/clients` | list/search/get Customer 360 | Client, Account, Transaction |
| `/entity-resolution` | get match candidates for a subject, confirm/reject a match | EntityResolutionMatch |
| `/evidence` | list evidence for a client | Evidence |
| `/risk` | get current score + factor breakdown + history | RiskScoreSnapshot |
| `/investigations` | list triggered investigations, get a report | InvestigationReport |
| `/timeline` | get chronological event list for a client | derived view |
| `/review` | list pending review queue, submit a decision | ReviewAction |
| `/sar` | get/list draft SARs, submit sign-off | DraftSAR |
| `/audit` | query the audit log (read-only, filterable) | AuditLogEntry |

No domain here requires anything beyond what §4's data model already supports.

---

## 6. Proposed Frontend Pages (conceptual — not built, technology TBD per audit §11)

1. **Alert/Review Queue** — triggered clients awaiting human review, sorted by score/recency.
2. **Customer 360** — single client's full profile: attributes, accounts, resolved sanctions
   candidates, UBO graph (if any), evidence list.
3. **Investigation View** — the structured investigation report, with every claim linked to its
   source evidence citation.
4. **Timeline View** — chronological visualization of evidence/score-change/investigation/review
   events for one client.
5. **SAR Review & Sign-off** — draft SAR content, editable/annotatable by a reviewer, with
   approve/reject actions.
6. **Audit Log Viewer** — searchable/filterable read-only view over `AuditLogEntry`.

A Streamlit-based build (already installed) is the pragmatic recommendation per audit §11 if a
custom frontend isn't warranted within the hackathon timeline; the page list above holds either
way.

---

## 7. Implementation Dependencies Between Future Phases

This mirrors audit §15, stated here in dependency-graph form:

```
Phase 1: Data layer (ingestion, normalization, canonical storage, tests against measured invariants)
    │
    ▼
Phase 2: Entity Resolution Service
    │
    ▼
Phase 3: Customer 360 Assembly
    │
    ▼
Phase 4: Deterministic Risk Scoring Engine   ← must be independently testable before any agent exists
    │
    ├──────────────┐
    ▼              ▼
Phase 5:        Phase 5b:
Monitoring +    Adverse Media Agent
Evidence        (test against adversarial_article.txt from day one)
Collection          │
    │◄───────────────┘
    ▼
Phase 6: High-Risk Trigger + Investigation Agent + Timeline
    │
    ├──────────────┐
    ▼              ▼
Phase 7:        Phase 8:
Human Review    SAR Drafting Agent
Workflow +      (depends on Phase 6's investigation report existing)
Audit Trail
(scaffold early,
exercise once
6/8 produce
real triggers)
    │              │
    └──────┬───────┘
           ▼
Phase 9: Frontend/API surface (built last, against a stable contract)
```

**Hard dependency rule carried forward from the audit:** Phase 4 (scoring) must exist and be
provably independent of any LLM call *before* Phase 5b (the first agent) is wired in. This is not
just good engineering order — it is the only way to demonstrate, rather than merely claim, that
the core design principle holds.

---

## 8. Open Question Before Phase 1

Per audit §14: whether the Tier-2 `sample_*.csv` sanctions fixtures should be treated as a primary
ingestible data source (this document's assumption) or purely as a reference/test fixture kept
separate from the main sanctions corpus. This affects whether Phase 1's ingestion layer loads them
into the same `SanctionsEntity` table (tagged `tier='tier2_demo_curated'`, as modeled in §4) or
keeps them out of the application database entirely and uses them only in test code. Recommend
confirming this before Phase 1 begins, since it changes the ingestion layer's scope.
