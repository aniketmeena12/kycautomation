# Investigation Engine -- Code Logic

**A code-reading companion.** What calls what, where it lives, and what shape the
data is in at each step.

> **For the *why*, read [`phase-5-investigation-agent.md`](phase-5-investigation-agent.md).**
> That file is the decision record: the rationale, the ADR links, the honesty
> arguments, the known limitations. This file deliberately does not restate them
> -- it points at the code and gets out of the way. Where a rule looks arbitrary
> here, the phase doc explains it.

---

## 1. Call chain

```
POST /api/v1/investigations/run/{external_client_id}      api/routes/investigations.py:72
POST /api/v1/investigations/{id}/rerun                     api/routes/investigations.py:96
      │
      ▼
InvestigationOrchestrator.run_for_client()                 investigation_service.py:157
                        .run_for_alert()                   investigation_service.py:175
                        .rerun()                           investigation_service.py:190
      │
      └──▶ _run()                                          investigation_service.py:217
             │
             ├─ 1. ContextBuilder.build(client)            context.py:114
             │       └─ reads 7 repositories, 0 network calls
             │
             ├─ 2. compute_context_hash(context)           investigation_service.py:125
             │
             ├─ 3. InvestigationAgent.investigate(ctx)     agent.py:100   ◀── the ONLY LLM call
             │       ├─ build_system_prompt()              prompts.py:330
             │       ├─ build_user_prompt(ctx)             prompts.py:336
             │       ├─ provider.complete_json(...)        llm_contracts.py:110  [gate 1]
             │       ├─ InvestigationReport.model_validate  schemas.py:235       [gate 2]
             │       └─ validate_report(report, ctx)       grounding.py:203      [gate 3]
             │
             ├─ 4. _persist(...)                           investigation_service.py:284
             │       └─ InvestigationRepository.create / add_finding / add_recommendation
             │
             ├─    db.commit()
             │
             └─ 5. record_audit_event(ActorType.AGENT)     services/audit_service.py
                   db.commit()
```

Exactly one step is non-deterministic. Step 1-2 decide what the model may know;
steps 4-5 decide what happens as a result. The model influences neither.

---

## 2. Module map

| File | Role | DB session? | Calls LLM? | Writes? |
|---|---|---|---|---|
| `investigation/schemas.py` | Context + report Pydantic models, `JSON_SCHEMA` | no | no | no |
| `investigation/context.py` | Assembles what the agent may see | **read-only** | no | no |
| `investigation/prompts.py` | Renders context -> two prompt strings | no | no | no |
| `investigation/grounding.py` | Injection scan + deterministic report verdict | no | **no** | no |
| `investigation/agent.py` | The one model call | **no** | **yes** | no |
| `services/investigation_service.py` | Orchestrates, decides status, persists, audits | yes | no | **yes** |
| `providers/llm_registry.py` | `LLM_PROVIDER` -> provider instance | no | no | no |
| `providers/{anthropic,groq}_llm_provider.py` | Vendor specifics, behind one Protocol | no | yes | no |

The two rows that matter: **`agent.py` has no session and no writer** --
AST-enforced, not convention (`test_investigation_agent.py:83`) -- and
**`grounding.py` never calls a model**, which currently holds by inspection only
(it imports `re`, pydantic, `core.enums`, `investigation.schemas`, and nothing
else). If you add an import there, nothing will stop you; the §11 rule is the
only guard.

---

## 3. Step 1 -- context assembly (`context.py:114`)

`ContextBuilder.build(client, trigger_reason=...)` constructs one
`InvestigationContext` from seven repositories:

| Builder method | Repository | Cap | Produces |
|---|---|---|---|
| `_build_client` | (the passed `Client`) | -- | `ContextClient` |
| `_build_evidence` | `EvidenceRepository` | `MAX_EVIDENCE = 40` | `list[ContextEvidenceItem]` |
| `_build_provider_coverage` | `EvidenceRepository` (`PROVIDER_RESPONSE` rows) | -- | `list[ContextProviderResult]` |
| `_build_risk` | `RiskSnapshotRepository.latest_for_client` | -- | `ContextRiskAssessment \| None` |
| `_build_events` | `RiskEventRepository` | `MAX_EVENTS = 30` | `list[ContextRiskEvent]` |
| `_build_matches` | `EntityMatchRepository.list_for_subject` | `MAX_MATCHES = 15` | `list[ContextEntityMatch]` |
| `_build_alerts` | `AlertRepository` | `MAX_ALERTS = 15` | `list[ContextAlert]` |
| `_build_transactions` | `TransactionRepository.summary_for_client` | -- | `ContextTransactionSummary` |

Three behaviours worth knowing before you edit this file:

**No live lookups.** It passes no live-lookup flags to `Customer360Service`, so
assembling context makes zero network calls and costs no provider budget. It
reads what the monitoring cycle *stored*. (Why: the report must describe the same
evidence the score was computed from.)

**Evidence is sorted by confidence, descending, then truncated** (`context.py:180`).
Truncation appends a `context_note` naming the real count -- so a thin report is
explainable afterwards.

**Absence produces a note, never a placeholder.** Every empty collection has a
matching branch appending to `notes`:

| Condition | Note |
|---|---|
| no evidence rows | "...an empty evidence base, not an absence of risk" |
| no risk snapshot | "never been scored... Run a monitoring cycle first" |
| no risk events | "No risk events have been recorded" |
| no provider rows | "cannot state which external checks were performed" |
| always | `OWNERSHIP_UNLINKED_NOTE` (`context.py:70`) |

`ownership` is **always** `[]`. The UBO fixtures and client master share no
identifier (Phase 0 §5), so there is no join to make; the note says so explicitly
rather than letting emptiness read as "ownership is simple".

### Injection scanning happens here

`context.py:195` runs `scan_for_injection()` over every `snippet` and
`extracted_fact`. Hits accumulate in `context.injection_flags` as
`"evidence:{id}: {pattern_name}"`. The text itself is **passed through
verbatim** -- quarantined at render time (§4), never rewritten.

### The output shape

```python
class InvestigationContext(BaseModel):          # schemas.py:184
    client:              ContextClient
    trigger_reason:      str
    risk_assessment:     ContextRiskAssessment | None
    risk_events:         list[ContextRiskEvent]
    entity_matches:      list[ContextEntityMatch]
    alerts:              list[ContextAlert]
    evidence:            list[ContextEvidenceItem]
    provider_results:    list[ContextProviderResult]
    transaction_summary: ContextTransactionSummary | None
    ownership:           list[ContextOwnershipNode]   # always []
    account_count:       int
    assembled_at:        datetime
    context_notes:       list[str]
    injection_flags:     list[str]

    @property
    def allowed_evidence_ids(self) -> set[int]:      # schemas.py:209
        return {item.evidence_id for item in self.evidence}
```

`allowed_evidence_ids` is the load-bearing property. Because the agent has no
tools and no DB, this set is *provably* the complete universe of ids it could
legitimately know -- which is the only reason grounding (§6) can work at all.

---

## 4. Step 3a -- prompt rendering (`prompts.py`)

Two channels, kept strictly separate.

**`build_system_prompt()` (`prompts.py:330`) takes no arguments.** Not a style
choice -- there is no parameter through which context could reach it. It returns
the `SYSTEM_PROMPT` constant: no f-string, no interpolation, no data. The
operator channel cannot be influenced by anything in the database.

**`build_user_prompt(context)` (`prompts.py:336`)** renders data only. Section
order:

```
# INVESTIGATION CONTEXT
  trigger, assembled_at
## CLIENT PROFILE
## DETERMINISTIC RISK ASSESSMENT (input, not yours to change)
## RISK EVENTS
## ENTITY RESOLUTION RESULTS (already decided; report only)
## ALERTS
## TRANSACTION SUMMARY
## OWNERSHIP / UBO STRUCTURE
## PROVIDER COVERAGE
## EVIDENCE (the ONLY citable facts)
## CONTEXT NOTES (from the assembling system)     [if any]
## CITABLE EVIDENCE IDS                            [always, last]
```

The allowlist is restated last, explicitly, as a closing constraint
(`prompts.py:365`). When it's empty the prompt says so and instructs the model to
report the gap rather than fill it.

### Quarantine (`prompts.py:232-239`)

Third-party text is wrapped:

```
  <untrusted_document evidence_id="7" source="adversarial_article">
  ...verbatim snippet, clipped to MAX_SNIPPET_CHARS=1500...
  </untrusted_document>
```

`neutralize_untrusted()` (`grounding.py:120`) rewrites any literal
`</untrusted_document>` inside the text to `&lt;/untrusted_document&gt;` -- the
payload stays byte-for-byte legible, but it cannot close its own block and escape
into the instruction stream. Escaping only; **not** redaction. An agent that
can't read a suspicious article can't investigate it.

The `SYSTEM_PROMPT`'s `UNTRUSTED CONTENT` section (`prompts.py:120-131`) is what
tells the model these blocks are data -- including the instruction to *report* an
injection attempt as a finding, since manipulation in a client's evidence file is
itself risk-relevant.

---

## 5. Step 3b -- the agent (`agent.py:100`)

```python
def investigate(self, context) -> AgentRunResult:   # never raises
    invocation = self._provider.complete_json(
        system_prompt=build_system_prompt(),
        user_prompt=build_user_prompt(context),
        json_schema=JSON_SCHEMA,                    # gate 1
        max_output_tokens=self._max_output_tokens,
    )
    if invocation.status is not SUCCESS:  return AgentRunResult(error=...)
    if invocation.parsed is None:         return AgentRunResult(error=...)
    report = InvestigationReport.model_validate(invocation.parsed)   # gate 2
    grounding = validate_report(report, context)                     # gate 3
    return AgentRunResult(invocation, report, grounding)
```

### Three gates, none redundant

| Gate | Where | Enforces | Cannot catch |
|---|---|---|---|
| 1 · JSON Schema | provider-side, `schemas.py::JSON_SCHEMA` | shape, required fields, action `enum` | "cite a *real* id" |
| 2 · Pydantic | `agent.py:127` | types, coercion, vocabulary again | semantic truth |
| 3 · Grounding | `grounding.py:203` | **do the cited ids exist** | -- |

A report that passes 1 and 2 and fails 3 is well-formed, correctly typed, and
fabricated. Gate 2 is not decoration: it catches a provider whose
constrained-output mode is weaker than advertised, which is why
`summary`/`reasoning`/`confidence_statement` are **required** with no
`""`-defaults (`schemas.py:254`) -- a gate that's more permissive than the one
before it is not a gate.

### `investigate()` never raises

A provider that is unconfigured / timed out / rate-limited / refused returns an
`AgentRunResult` with `error` set and `report=None`. Failure is an ordinary
operational fact to be persisted, not an exception to be caught somewhere else.
Raising would turn a coverage gap into a 500 instead of a record.

### `succeeded` is `report is not None` (`agent.py:81`)

**Grounding failure does not flip it.** A report citing a fabricated id was still
generated, and gets persisted and shown -- flagged. `grounding.passed` is the
separate, honest signal for whether to believe the content. Hiding a
hallucination behind a `FAILED` status would erase the single most important
thing a reviewer could learn.

### Report shape

```python
class InvestigationReport(BaseModel):        # schemas.py:235
    summary:              str                # required
    reasoning:            str                # required -- authored rationale, NOT chain-of-thought
    confidence_statement: str                # required -- prose; there is no numeric field
    key_findings:         list[ReportFinding]         # default []
    supporting_evidence:  list[ReportFinding]         # default []
    conflicting_evidence: list[ReportFinding]         # default []
    missing_information:  list[str]
    recommendations:      list[ReportRecommendation]
    limitations:          list[str]
    citations:            list[int]

class ReportFinding(BaseModel):              # schemas.py:219
    finding: str; evidence_ids: list[int]; confidence_statement: str
```

The collections default to empty because **empty is a meaningful, correct value**
-- "the evidence supports no key findings" is a legitimate and, on a thin
evidence base, desirable outcome. Requiring non-empty would pressure the model to
invent content to satisfy a validator.

---

## 6. Step 3c -- grounding (`grounding.py:203`)

```python
def validate_report(report, context) -> GroundingReport:
    allowed = context.allowed_evidence_ids

    cited_everywhere  = ⋃ finding.evidence_ids            # key + supporting + conflicting
                      ∪ ⋃ recommendation.evidence_ids
                      ∪ report.citations

    hallucinated = cited_everywhere - allowed             # fabrication
    used         = cited_everywhere & allowed
    ignored      = allowed - cited_everywhere             # coverage signal, not a failure

    passed = not hallucinated and not illegal
```

Citations are collected from **everywhere**, not just `report.citations` -- else a
finding could cite a fabricated id as long as the summary list stayed clean.

### Per-finding status (`grounding.py:182`)

| Cited ids | Status |
|---|---|
| any id ∉ allowed | `UNGROUNDED` |
| none at all | `UNCITED` |
| all ∈ allowed | `GROUNDED` |

**`UNCITED` is not a hard failure.** "No adverse media provider was configured, so
this was not checked" is true, useful, and has no `Evidence` row to point at.
Forcing a citation there would push the model to attach an unrelated id to satisfy
the validator. It is still counted (`uncited_finding_count`).

`passed` is False on exactly two things: **a fabricated citation**, or **a
recommendation outside the vocabulary**.

### The vocabulary (`core/enums.py:229`)

```
CONTINUE_MONITORING · REQUEST_DOCUMENTATION · ENHANCED_DUE_DILIGENCE
ESCALATE · DRAFT_SAR_REVIEW · CLOSE_INVESTIGATION
```

**No `APPROVE`. No `REJECT`.** Absent by design. Enforced three times over: the
`enum` in the emitted JSON schema, the enum-typed Pydantic field
(`ReportRecommendation.action`), and the re-check at `grounding.py:245`. The third
is defence in depth against the schema ever being relaxed -- one set lookup to
guard the exact thing this phase must never do.

### Injection patterns (`grounding.py:53`)

`instruction_override` · `role_reassignment` · `fake_turn_marker` ·
`fake_tag_marker` · `prompt_exfiltration` · `verdict_steering` ·
`score_steering` · `recommendation_steering`

This is a **detector, not the defence**. The defence is structural: untrusted
text only ever enters the user turn, inside quarantine, with the operator channel
stating it is data. Pattern-matching can't be exhaustive; treating it as the
control would be theatre. It exists so an attempt becomes a *recorded, visible
fact*. Note `score_steering` includes the verbs `mark`/`report` specifically
because the canonical attack on this system -- and the phrasing in
`data/articles/adversarial_article.txt` -- is "mark risk score 0". A detector that
misses the attack in its own corpus is not a detector.

---

## 7. Step 2 -- `context_hash` (`investigation_service.py:125`)

```python
_HASH_EXCLUDED_FIELDS = {"assembled_at", "trigger_reason", "context_notes", "injection_flags"}

def compute_context_hash(context) -> str:
    payload = context.model_dump(mode="json", exclude=_HASH_EXCLUDED_FIELDS)
    return sha256(json.dumps(payload, sort_keys=True, default=str)).hexdigest()
```

The hash answers exactly one question: *has the evidence changed since last time?*
So it must fingerprint the **evidence picture**, never the **run**.

| Excluded | Because |
|---|---|
| `assembled_at` | a timestamp; changes on every call |
| `trigger_reason` | **a re-run's reason embeds the original's id** ("Re-run of investigation #1...") -- including it guarantees a re-run can never match its own original, breaking the mechanism precisely where it is used |
| `context_notes` | derived commentary; carries the re-run annotation |
| `injection_flags` | derived from evidence already being hashed |

What remains is the substance: client, score, events, matches, alerts, evidence,
coverage, transactions. Two equal hashes then genuinely mean the model was shown
the same picture twice.

> This is the same trap as Phase 4's `dedup_key` (ADR-019), and it shipped broken
> in Phase 5's first cut. It was found by *running a re-run*, not by a unit test.

`rerun()` (`investigation_service.py:190`) passes the original's hash in as
`previous_context_hash`; `_run` compares and appends one of two notes
(`investigation_service.py:230`):

- match -> "Evidence base is UNCHANGED... any difference in this report is model variance, not new information."
- differ -> "Evidence base has CHANGED since the previous run."

---

## 8. Steps 4-5 -- persist + audit (`investigation_service.py:284`)

### Status assignment -- the whole ballgame

```python
status = InvestigationStatus.AWAITING_HUMAN_REVIEW if run.succeeded \
         else InvestigationStatus.FAILED
```

That is the complete set of statuses this path can produce. **Never `CLOSED`,
never `ESCALATED` -- even when the agent recommends `ESCALATE`.** A recommendation
is an input to a human's decision; if it could set the status, the agent would be
deciding and the vocabulary would be a formality. `ESCALATED`/`CLOSED` are
reachable only through `CaseService.apply_review` (ADR-029, ADR-035).

`FAILED` is deliberately not `CLOSED`: "we investigated and closed it" and "we
could not investigate" are opposite facts.

### Three tables

| Table | Written by | Notable columns |
|---|---|---|
| `investigations` | `.create()` | `status`, `context_hash`, `prompt_version`, `llm_provider`, `llm_model`, `temperature`, `latency_ms`, `input/output_tokens`, `grounding_passed`, `hallucinated_citation_count`, `evidence_used_count`, `evidence_available_count`, `report_json`, `grounding_json`, `injection_flags_json`, `error_message` |
| `investigation_findings` | `.add_finding()` | `finding_type`, `grounding_status`, `evidence_id` (FK), `cited_evidence_ids_json`, `invalid_evidence_ids_json` |
| `investigation_recommendations` | `.add_recommendation()` | `action`, `rationale`, `cited_evidence_ids_json` |

`report_json` / `grounding_json` go through `_bounded()`
(`investigation_service.py:94`, caps 60k / 20k chars) -- a `Text` column must not
become an unbounded dump, and truncation is explicit in the stored value.

### `_add_finding` -- valid ids only in the FK (`investigation_service.py:360`)

```python
evidence_id = valid_ids[0] if valid_ids else None      # the FK column
cited_evidence_ids_json = json.dumps(finding.evidence_ids)   # everything, valid or not
```

An invented id has no row to point at, so the FK write would fail -- correctly.
But the **full cited list is preserved** in `cited_evidence_ids_json`, and the
invalid ones separately in `invalid_evidence_ids_json`. Nothing is hidden. A bad
finding is **flagged, never deleted**: dropping it would erase the evidence that
this model hallucinated on this client's file, and make the report look cleaner
than the run was.

Findings are matched to their grounding verdict **by text**
(`investigation_service.py:340`) -- that is what `GroundingReport.findings`
carries back.

### Audit (`investigation_service.py:259`)

`record_audit_event(actor_type=ActorType.AGENT, actor_id=f"investigation_agent:{provider}:{model}", action="investigation_run", ...)`.
Phase 1 defined `ActorType.AGENT` for exactly this moment. The `new_value` payload
carries `investigation_id`, `status`, `prompt_version`, `model`, `provider`,
`context_hash`, `grounding_passed`, `hallucinated_citations`, `latency_ms`,
`error`.

Two commits: one after `_persist`, one after the audit event.

---

## 9. The provider seam (`llm_registry.py`)

```python
_FACTORIES = {
    "anthropic": lambda s: AnthropicLLMProvider(s),
    "groq":      lambda s: GroqLLMProvider(s),
}
```

Adding a vendor = write a class satisfying the `LLMProvider` Protocol, add one
line here, set `LLM_PROVIDER` / `LLM_MODEL` (or `GROQ_MODEL`) in `.env`. Nothing
in the agent, orchestrator, prompts, grounding, persistence, or API imports a
vendor SDK or names a vendor -- pinned by
`test_no_component_outside_the_provider_layer_mentions_groq`.

`get_llm_provider()` fails fast on an unknown name and **`isinstance`-checks the
Protocol** at resolution -- a half-written provider is caught there with a
readable error, not with an `AttributeError` deep inside an investigation.

Both SDKs are **optional**, imported lazily *inside* provider methods (ADR-023).
The app and the whole suite run with both uninstalled. Never move those imports
to module scope.

`complete_json()` returns `LLMInvocationResult` (`llm_contracts.py:50`):
`status`, `provider`, `model`, `parsed`, `text`, `input_tokens`, `output_tokens`,
`latency_ms`, `temperature`, `stop_reason`, `error_message`, `invoked_at`.

---

## 10. API surface (`api/routes/investigations.py`)

| Method | Path | Line |
|---|---|---|
| `GET` | `/api/v1/investigations/agent/status` | 61 |
| `POST` | `/api/v1/investigations/run/{external_client_id}` | 72 |
| `POST` | `/api/v1/investigations/{investigation_id}/rerun` | 96 |
| `GET` | `/api/v1/investigations/client/{external_client_id}` | 108 |
| `GET` | `/api/v1/investigations/{investigation_id}` | 130 |

`agent_status()` (`investigation_service.py:406`) returns `provider`, `model`,
`configured`, `prompt_version` -- no key material.

---

## 11. If you change this code, keep these true

- `agent.py` imports no `sqlalchemy`, no `app.risk`, no `app.resolution`, no
  `app.repositories`, no `app.services`, no `app.models` --
  `test_agent_cannot_reach_the_database_the_risk_engine_or_any_writer`
  (`test_investigation_agent.py:83`) parses its AST and asserts it. It is the
  mirror of `test_engine_module_imports_nothing_that_could_reach_an_llm_or_the_network`
  (`test_risk_engine.py:298`), which asserts the risk engine imports no model SDK,
  HTTP client, or DB session. **The two pin the boundary from both sides**: the
  scorer cannot reach a model, and the model-caller cannot reach the scorer.
  Neither can be satisfied by a comment.
- `grounding.py` calls no model. The thing judging the LLM must not be an LLM.
- `build_system_prompt()` takes no arguments.
- `InvestigationRecommendationAction` gains no `APPROVE`/`REJECT` (ADR-027).
- The success path terminates at `AWAITING_HUMAN_REVIEW` (ADR-029).
- `thinking.display` stays `"omitted"`; never send `temperature`/`top_p`/`top_k`
  (ADR-025, ADR-026). On Groq use `include_reasoning=False` -- `reasoning_format="hidden"`
  is the wrong lever and silently violates ADR-026.
- Anything excluded from `context_hash` stays excluded, and anything you add to
  `InvestigationContext` gets considered against `_HASH_EXCLUDED_FIELDS`.
- Untrusted text is quarantined, never rewritten.
- A hallucinated citation is flagged and stored, never dropped.

---

## 12. Related

- [`phase-5-investigation-agent.md`](phase-5-investigation-agent.md) -- rationale, ADRs, verification, limitations
- [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md) -- ADR-021, 023-031, 035
- [`phase-4-risk-intelligence.md`](phase-4-risk-intelligence.md) -- where the score and the events come from
- [`phase-6-case-management.md`](phase-6-case-management.md) -- where `AWAITING_HUMAN_REVIEW` goes next
- `backend/tests/test_investigation_agent.py` · `test_investigation_grounding.py` · `test_investigation_service.py` · `test_investigations_api.py`
