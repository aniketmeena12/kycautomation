# Phase 6 -- Enterprise Case Management

**Continuous KYC Autonomous Auditor**
**Status:** complete. Closes the compliance workflow: case workspace, generated
timeline, human review, Draft SAR, immutable audit. No frontend, no dashboard,
no new monitoring/risk/resolution logic — everything here is built on Phases 0–5.

---

## 1. Case lifecycle

```
        (alert / investigation / manual)
                    │
                    ▼
   ┌────────────► OPEN ──────────────┐
   │               │                  │
   │               ▼                  │
   │         UNDER_REVIEW ◄───────┐   │
   │           │        │          │   │
   │           ▼        ▼          │   │
   │      ESCALATED   SAR_REVIEW ──┘   │   (reject draft → back for work)
   │           │        │              │
   └───────────┴────────┴──────────────┴──► CLOSED  (terminal)
```

Every transition is validated by `app/casework/state_machine.py` — pure
functions, no I/O, same discipline as `app/risk/engine.py`. An illegal move
raises; the API returns **409** and names the actions that *are* permitted now.

**CLOSED is terminal.** Reopening a closed case would overwrite the fact that it
was closed — and that fact, with its reviewer and timestamp, is what an auditor
came to see. The honest way to revisit one is a new case, which is why
`case_ref` exists.

---

## 2. Case architecture: an anchor, not a copy

`Case` stores **only what is its own**: lifecycle state, assignee, open/close
timestamps and reasons. It stores no score, no summary, no evidence.

That is the single most important decision in this phase. Copying the
investigation summary and risk score onto the case would make reads convenient
and create a second source of truth that silently drifts. **A workspace showing
a stale score next to a live investigation is worse than no workspace.** So
`CaseService` aggregates at request time through foreign keys, and
`test_case_aggregates_the_client_without_copying_it` asserts the columns never
grow a duplicate.

The workspace (`GET /cases/{id}`) assembles: Customer 360, current risk +
history, risk events, entity matches, evidence, alerts, investigations, reviews,
SAR drafts, and `available_actions`.

Customer 360 is called with **live lookups off** (ADR-009): opening a case must
not fire provider queries, and the workspace must show what the score was
computed *from*, not a fresher picture that would disagree with it.

---

## 3. Timeline engine

> "Never manually assemble timelines. Generate from stored events."

Taken literally. `TimelineBuilder` has exactly one public method — `build` —
and no `add_entry()`. There is no way to put something on a timeline that did
not happen and get recorded. Nine collectors project rows from
`risk_score_snapshots`, `risk_events`, `evidence`, `entity_matches`, `alerts`,
`investigations`, `human_reviews`, and `sar_drafts`.

**Three rules it enforces:**

| Rule | Mechanism | Why |
|---|---|---|
| No duplicates | `entry_key = "{type}:{source_id}"` | Keying on the rendered title would collapse two distinct events with identical wording — silently deleting history. |
| Every entry has an actor | SYSTEM / AGENT / HUMAN | An LLM's opinion and a compliance officer's decision must never look alike. |
| Deterministic order | sort by `(timestamp, entry_key)` | A monitoring cycle writes a snapshot *and* several events in the same instant; without the tiebreaker two reads disagree and no reviewer trusts it. |

Three details worth noting:
- A snapshot yields **two** entries — the cycle ran, and (only if `delta != 0`)
  the score moved. Emitting RISK_SCORE_CHANGE every cycle would bury the real
  changes under noise (the reasoning of ADR-019).
- `PROVIDER_RESPONSE` evidence renders as **PROVIDER_RESULT**, not EVIDENCE. It
  is a statement about *coverage* — "we checked and found nothing" must not read
  as a finding.
- A **failed** investigation shows its error, not a blank (ADR-021/029 lineage).

Read-only, like the resolution pipeline (ADR-015) and the Phase 5 context builder.

**A bug this caught:** SQLite returns naive datetimes even for
`DateTime(timezone=True)` columns. Sorting naive and aware datetimes together
raises `TypeError`, so a timeline mixing them would crash on the first case that
had both — i.e. every real case. `_utc()` normalises; rule 3 depends on it.

---

## 4. Human review workflow

Every review writes **three things, in this order**:

1. the validated transition (illegal → raises, writes *nothing*),
2. the `HumanReview` row (append-only),
3. the `AuditLog` row (`ActorType.HUMAN`, named reviewer).

Order matters: a review recorded for a transition that was rejected would be a
lie in the audit trail. `test_an_illegal_action_writes_nothing_at_all` pins it.

Each review stores reviewer, timestamp, action, comment, **previous state**, and
**new state**. "Never overwrite reviews" is a write path that does not exist:
`CaseRepository` has no update/delete for reviews. A reviewer who changes their
mind records a *new* one — "escalated, then closed an hour later" and "closed"
are different facts and only the first is true.

`reviewer` is **required with no default**. An unattributed compliance decision
is not a compliance decision.

### This is the phase the human-only states were reserved for

| Reserved by | State | Unlocked here by |
|---|---|---|
| ADR-016 (Phase 3) | `EntityMatchStatus.CONFIRMED` / `HUMAN_REVIEWED` | `CONFIRM_MATCH` / `REJECT_MATCH` |
| ADR-029 (Phase 5) | `InvestigationStatus.ESCALATED` / `CLOSED` | `ESCALATE` / `CLOSE_CASE` |
| Phase 1 | `SARStatus.APPROVED` / `REJECTED` | `APPROVE_DRAFT_SAR` / `REJECT_DRAFT_SAR` |

`CaseService.apply_review` is the **only** writer of any of them, and the
authority is always a named person — never a model, never a schedule. A reviewer
also cannot adjudicate another client's match by guessing an id
(`test_a_reviewer_cannot_adjudicate_another_clients_match`).

---

## 5. SAR workflow

> "The LLM may assist only with narrative. The LLM must never invent evidence."

**Eight of nine sections are deterministic Python over stored rows. Exactly one
— the Executive Summary — comes from a model**, and it is written *after* the
factual sections exist, *from* those sections.

So "the LLM invented a transaction in the SAR" is not guarded by a prompt; it is
**unreachable**. The narrative schema has no field that could carry a date, an
amount, an entity, or an evidence row — only prose and citations. Nothing it
returns is merged into the factual sections.
`test_only_the_narrative_is_llm_generated` asserts the split per section.

| # | Section | Source |
|---|---|---|
| 1 | Subject Information | deterministic |
| 2 | **Executive Summary** | **LLM narrative** (grounding-checked) |
| 3 | Chronology | deterministic (from the generated timeline) |
| 4 | Risk Indicators | deterministic (the Phase 4 score + factors) |
| 5 | Supporting Evidence | deterministic |
| 6 | Investigation Findings | deterministic (transcribed from Phase 5) |
| 7 | Recommendations | deterministic |
| 8 | Reviewer Notes | deterministic — **intentionally blank** |
| 9 | Disclaimer | deterministic |

**The narrative is still grounding-checked.** It cannot *add* evidence, but it
can still *cite* an id that does not exist — in a document that reads as a
filing. The same validator that guards investigations
(`app/investigation/grounding.py`) runs over it; a failure is written into the
document itself as a WARNING, so a reviewer sees it without opening the database.

**Reviewer Notes are never machine-populated.** A system that pre-filled them
would be putting words in the mouth of the person accountable for the filing.

**No LLM? The SAR is still generated**, with a plainly-worded placeholder. A SAR
is a factual document whose facts are deterministic; the absence of a model must
never be the reason a compliance officer has no draft to read.

**Always DRAFT.** Nothing in `SARGenerator` or `CaseRepository` can set
`APPROVED` — only a human's `APPROVE_DRAFT_SAR`. There is no FILED state and no
endpoint that files. Approving does **not** close the case: filing is out of
scope, and a case that closed itself on approval would assert an outcome nobody
recorded.

---

## 6. Audit trail

Immutable **by omission**: `AuditLogRepository` exposes `create` and reads. No
update, no delete — asserted by test. Every row carries timestamp, actor
(SYSTEM/AGENT/HUMAN), actor id, action, target, old value, new value, reason,
correlation id.

`GET /cases/{id}/audit` correlates by **target**, not by a single id: one case's
story spans the case itself, its client's monitoring cycles, its investigations,
and its SAR drafts. A trail showing only rows literally targeting `Case` would
omit the reason the case exists.

The three actors stay distinct end-to-end: monitoring is SYSTEM, the
investigation agent is AGENT (Phase 5), a reviewer is HUMAN.

---

## 7. Metrics (brief §8)

Open / under-review / escalated / SAR-review / closed counts, high-risk cases,
SAR pending / approved / rejected, human review counts by action, investigations
total/failed, and mean investigation latency.

Latency averages **only investigations that produced a report** — including
failures would average in calls that returned nothing and make a broken provider
look fast. It is `null`, not `0.0`, when none have run: `0.0` reads as "instant".

Deliberately **no** SAR approval rate, reviewer accuracy, or quality score.
Phase 0 §14 established this dataset cannot support calibration, and rating a
human reviewer's judgement against an unvalidated baseline would be exactly the
unearned metric this project has refused since then.

---

## 8. APIs

| Method & path | Purpose |
|---|---|
| `GET /api/v1/cases` | The queue (thin projection; filterable by status/assignee) |
| `GET /api/v1/cases/metrics` | Brief §8 |
| `POST /api/v1/cases` | Open a case (idempotent per active case) |
| `GET /api/v1/cases/{id}` | The workspace + `available_actions` |
| `GET /api/v1/cases/{id}/timeline` | Generated chronology |
| `POST /api/v1/cases/{id}/review` | A human decision |
| `GET /api/v1/cases/{id}/audit` | Immutable trail |
| `POST /api/v1/cases/{id}/sar` | Generate a Draft SAR → `SAR_REVIEW` |
| `GET /api/v1/cases/{id}/sar` | Read the Draft SAR |

**Nothing here decides.** There is no `/close`, `/approve`, `/confirm`, or
`/file` path, and no `PUT`/`PATCH`/`DELETE` — asserted by test. Every
consequential action exists only as a reviewer *action* on `/review`, requiring
a named reviewer. No script can complete a compliance decision no human made.

`available_actions` comes from the state machine so a caller never guesses — and
a future UI cannot render a button the server will reject.

---

## 9. Validation (brief §10)

| Requirement | How it holds |
|---|---|
| Timeline generated entirely from stored events | 9 collectors, no `add_entry`, `test_timeline_has_no_public_append_method` |
| No duplicate timeline entries | `entry_key` dedup + test |
| Every review action produces an audit record | written in `apply_review`, asserted per action |
| Every SAR references evidence IDs | `cited_evidence_ids_json` + `test_sar_references_evidence_ids` (asserts ⊆ real ids) |
| No hallucinated facts | narrative schema cannot carry facts; citations grounding-checked; failures flagged in-document |

---

## 10. Tests

**76 Phase 6 tests** (28 state machine, 35 service/timeline/SAR, 13 API).
**476/476 pass** suite-wide. No regressions.

Highlights: an illegal action writing *nothing*; reviews append-only across a
mind-change; `CONFIRM_MATCH` being the only route to `CONFIRMED`; a reviewer
blocked from another client's match; the timeline deterministic across two
reads; SAR sections proven deterministic except the narrative; a hallucinated
narrative flagged in the document; a SAR generated with no LLM at all; and the
API exposing no path that decides.

---

## 11. Known limitations

1. **No authentication.** `reviewer` is a caller-supplied string. Real
   deployment needs identity; the workflow records *who claimed* to decide, and
   nothing verifies it. Out of scope and stated rather than implied.
2. **No SAR filing, and no FILED state.** Approving means "fit to file". This
   system does not transmit to any authority.
3. **Timeline is unpaginated**, bounded at 200 rows per source. A multi-year
   case would need cursoring.
4. **Audit correlation is by target**, not a true correlation-id graph. A
   `correlation_id` is stored and passed through, but the trail query joins on
   target type/id.
5. **Case assignment is a string field.** No queue, no workload balancing, no
   notifications.
6. **`_summarize` and `audit_trail` issue per-case queries.** Fine at this
   scale (single-writer SQLite, ADR-001); a 10k-case queue would need
   denormalised counters — which is exactly the trade §2 refuses today.
7. **`open_case_for_client` is idempotent per active case**, so a client cannot
   have two concurrent cases. Deliberate, but it means parallel investigations
   of the same subject share one workspace.

---

## 12. Boundary: what Phase 6 did NOT build

No frontend, no dashboard, no new monitoring, no new risk engine, no new entity
resolution. The metrics endpoint exists to *support* a future dashboard; it does
not render one.

The LLM's role did not grow: it writes one narrative paragraph, in a document
whose facts were assembled before it was called, and it cannot approve, close,
score, resolve, or decide anything.
