"""
InvestigationOrchestrator -- the deterministic spine of Phase 5 (brief SS2).

    Alert / risk state
        -> Investigation trigger
        -> Evidence collection      (ContextBuilder; reads the DB, invents nothing)
        -> Context assembly         (InvestigationContext + context_hash)
        -> LLM investigation        (InvestigationAgent -- the ONLY model call)
        -> Structured findings      (validated, then grounded)
        -> Recommendations          (vocabulary-constrained)
        -> Persist + audit

THE DIVISION OF LABOUR IS THE WHOLE POINT
------------------------------------------
Everything on that list except one step is deterministic code. The agent
explains; this service decides what happens as a result. It owns the status
transitions, the persistence, and the audit record -- none of which the model
can influence, because the model never sees them.

Concretely, the orchestrator NEVER lets the agent:
  * change a risk score            (it reads the stored snapshot; nothing here writes one)
  * create a risk event or alert   (no RiskEventRepository, no AlertRepository imported)
  * modify evidence                (EvidenceRepository is used read-only, via ContextBuilder)
  * perform entity resolution      (EntityMatch rows are read; the pipeline is not invoked)
  * decide a compliance outcome    (terminal status is AWAITING_HUMAN_REVIEW -- see below)

TERMINAL STATUS IS ALWAYS AWAITING_HUMAN_REVIEW
------------------------------------------------
A successful investigation ends in AWAITING_HUMAN_REVIEW, never CLOSED and
never ESCALATED -- even when the agent recommends escalation. A recommendation
is an input to a human's decision; if the agent's recommendation could set the
status, the agent would be deciding, and the recommendation vocabulary
(ADR-027) would be a formality. ESCALATED and CLOSED stay unreachable from
every automated path. See ADR-029.

A FAILED RUN IS RECORDED, NOT SWALLOWED
----------------------------------------
Provider unconfigured, timed out, rate-limited, refused? The Investigation row
is still written, with status FAILED and the reason. An investigation that
silently does not happen is indistinguishable from one that found nothing --
the same reasoning that makes a provider failure a zero-weight risk event in
Phase 4 (ADR-021).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.enums import (
    ActorType,
    GroundingStatus,
    InvestigationFindingType,
    InvestigationStatus,
)
from app.investigation.agent import AgentRunResult, InvestigationAgent
from app.investigation.context import ContextBuilder
from app.investigation.prompts import PROMPT_VERSION
from app.investigation.schemas import InvestigationContext, ReportFinding
from app.models.client import Client
from app.models.investigation import Investigation
from app.providers.llm_registry import get_llm_provider
from app.repositories.client_repository import ClientRepository
from app.repositories.investigation_repository import InvestigationRepository
from app.repositories.risk_repository import AlertRepository, RiskSnapshotRepository
from app.services.audit_service import record_audit_event

MAX_REPORT_JSON_CHARS = 60_000
MAX_GROUNDING_JSON_CHARS = 20_000

DEFAULT_TRIGGER_REASON = "Manual investigation request."


class ClientNotFoundError(Exception):
    def __init__(self, external_client_id: int) -> None:
        self.external_client_id = external_client_id
        super().__init__(f"No client with external_client_id={external_client_id}.")


class InvestigationNotFoundError(Exception):
    def __init__(self, investigation_id: int) -> None:
        self.investigation_id = investigation_id
        super().__init__(f"No investigation with id={investigation_id}.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded(payload: object, limit: int) -> str:
    """JSON, bounded. Mirrors the truncation discipline in evidence_service.py
    and monitoring_service.py -- a Text column must not become an unbounded
    dump. Truncation is explicit in the stored value, never silent."""
    encoded = json.dumps(payload, default=str)
    if len(encoded) > limit:
        return json.dumps({"truncated": True, "original_length": len(encoded)})
    return encoded


# Fields excluded from the context hash. Every one of them varies per RUN
# rather than per EVIDENCE PICTURE, and the hash exists to answer exactly one
# question: "has the evidence changed since last time?"
#
# This is the same trap Phase 4 documents for dedup_key (ADR-019): fingerprint
# the FINDING, never the observation. A hash over run-specific fields is unique
# every time and therefore answers nothing.
#
#   assembled_at    -- a timestamp; changes on every single call.
#   trigger_reason  -- a rerun's reason embeds the original's id, so including
#                      it guarantees a rerun NEVER matches its own original --
#                      breaking the mechanism precisely where it is used.
#   context_notes   -- derived commentary, and carries the rerun annotation.
#   injection_flags -- derived from the evidence already being hashed.
#
# What remains is the substance: client, score, events, matches, alerts,
# evidence, coverage, transactions. Two equal hashes then genuinely mean the
# model was shown the same picture twice.
_HASH_EXCLUDED_FIELDS = {"assembled_at", "trigger_reason", "context_notes", "injection_flags"}


def compute_context_hash(context: InvestigationContext) -> str:
    """A stable fingerprint of the evidence picture the model was shown."""
    payload = context.model_dump(mode="json", exclude=_HASH_EXCLUDED_FIELDS)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class InvestigationOrchestrator:
    def __init__(
        self,
        db: Session,
        agent: InvestigationAgent | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        # Injectable so a test can supply a deterministic provider without any
        # network, and so swapping vendors is a construction detail rather than
        # a change here.
        self._agent = agent or InvestigationAgent(
            get_llm_provider(self._settings),
            max_output_tokens=self._settings.llm_max_output_tokens,
        )
        self._clients = ClientRepository(db)
        self._investigations = InvestigationRepository(db)
        self._snapshots = RiskSnapshotRepository(db)
        self._alerts = AlertRepository(db)
        self._context_builder = ContextBuilder(db)

    # ------------------------------------------------------------------ #
    # Entry points
    # ------------------------------------------------------------------ #

    def run_for_client(
        self,
        external_client_id: int,
        *,
        trigger_reason: str | None = None,
        alert_id: int | None = None,
        correlation_id: str | None = None,
    ) -> Investigation:
        client = self._clients.get_by_external_id(external_client_id)
        if client is None:
            raise ClientNotFoundError(external_client_id)
        return self._run(
            client,
            trigger_reason=trigger_reason or DEFAULT_TRIGGER_REASON,
            alert_id=alert_id,
            correlation_id=correlation_id,
        )

    def run_for_alert(self, alert_id: int, *, correlation_id: str | None = None) -> Investigation:
        """Alert -> Investigation, the brief's primary trigger."""
        alert = self._alerts.get_by_id(alert_id)
        if alert is None:
            raise InvestigationNotFoundError(alert_id)
        client = self._clients.get_by_id(alert.client_id)
        if client is None:
            raise ClientNotFoundError(alert.client_id)
        return self._run(
            client,
            trigger_reason=f"Alert #{alert.id} ({alert.trigger.value}): {alert.reason or 'no reason recorded'}",
            alert_id=alert.id,
            correlation_id=correlation_id,
        )

    def rerun(self, investigation_id: int, *, correlation_id: str | None = None) -> Investigation:
        """Re-investigate a client, producing a NEW row.

        Never mutates the original. An investigation is a record of what was
        concluded at a point in time, from evidence available at that time;
        overwriting it would destroy the ability to see that the conclusion
        changed -- which is the only reason to re-run.
        """
        original = self._investigations.get_by_id(investigation_id)
        if original is None:
            raise InvestigationNotFoundError(investigation_id)
        client = self._clients.get_by_id(original.client_id)
        if client is None:
            raise ClientNotFoundError(original.client_id)

        return self._run(
            client,
            trigger_reason=f"Re-run of investigation #{original.id}. {original.trigger_reason or ''}".strip(),
            alert_id=original.triggering_alert_id,
            correlation_id=correlation_id,
            previous_context_hash=original.context_hash,
        )

    # ------------------------------------------------------------------ #
    # The cycle
    # ------------------------------------------------------------------ #

    def _run(
        self,
        client: Client,
        *,
        trigger_reason: str,
        alert_id: int | None,
        correlation_id: str | None,
        previous_context_hash: str | None = None,
    ) -> Investigation:
        # 1. Assemble the grounded context. Read-only; no provider calls.
        context = self._context_builder.build(client, trigger_reason=trigger_reason)
        context_hash = compute_context_hash(context)

        if previous_context_hash is not None:
            context.context_notes.append(
                "Evidence base is UNCHANGED since the previous run; any difference in this report is "
                "model variance, not new information."
                if previous_context_hash == context_hash
                else "Evidence base has CHANGED since the previous run."
            )

        snapshot = self._snapshots.latest_for_client(client.id)

        # 2. The one model call.
        run = self._agent.investigate(context)

        # 3. Persist -- success and failure alike.
        investigation = self._persist(
            client=client,
            context=context,
            context_hash=context_hash,
            run=run,
            trigger_reason=trigger_reason,
            alert_id=alert_id,
            snapshot_id=snapshot.id if snapshot else None,
        )

        self._db.commit()

        # 4. Audit. ActorType.AGENT -- Phase 1 defined this actor for exactly
        #    this moment, and it is the first time anything in this project has
        #    legitimately used it.
        record_audit_event(
            self._db,
            actor_type=ActorType.AGENT,
            actor_id=f"investigation_agent:{self._agent.provider_name}:{self._agent.model}",
            action="investigation_run",
            target_type="Client",
            target_id=str(client.external_client_id),
            reason=trigger_reason,
            new_value={
                "investigation_id": investigation.id,
                "status": investigation.status.value,
                "prompt_version": investigation.prompt_version,
                "model": investigation.llm_model,
                "provider": investigation.llm_provider,
                "context_hash": context_hash,
                "grounding_passed": investigation.grounding_passed,
                "hallucinated_citations": investigation.hallucinated_citation_count,
                "latency_ms": investigation.latency_ms,
                "error": investigation.error_message,
            },
            correlation_id=correlation_id,
        )
        self._db.commit()
        return investigation

    def _persist(
        self,
        *,
        client: Client,
        context: InvestigationContext,
        context_hash: str,
        run: AgentRunResult,
        trigger_reason: str,
        alert_id: int | None,
        snapshot_id: int | None,
    ) -> Investigation:
        invocation = run.invocation
        evidence_available = len(context.allowed_evidence_ids)

        investigation = self._investigations.create(
            client_id=client.id,
            # Successful runs stop at AWAITING_HUMAN_REVIEW -- never CLOSED,
            # never ESCALATED, regardless of what the agent recommended.
            status=InvestigationStatus.AWAITING_HUMAN_REVIEW if run.succeeded else InvestigationStatus.FAILED,
            trigger_snapshot_id=snapshot_id,
            triggering_alert_id=alert_id,
            trigger_reason=trigger_reason,
            opened_at=_now(),
            summary=run.report.summary if run.report else None,
            context_hash=context_hash,
            prompt_version=run.prompt_version or PROMPT_VERSION,
            llm_provider=invocation.provider,
            llm_model=invocation.model,
            latency_ms=invocation.latency_ms,
            input_tokens=invocation.input_tokens,
            output_tokens=invocation.output_tokens,
            temperature=invocation.temperature,
            generated_at=invocation.invoked_at,
            report_json=(
                _bounded(run.report.model_dump(mode="json"), MAX_REPORT_JSON_CHARS) if run.report else None
            ),
            grounding_passed=run.grounding.passed if run.grounding else None,
            hallucinated_citation_count=(
                len(run.grounding.hallucinated_evidence_ids) if run.grounding else None
            ),
            evidence_used_count=len(run.grounding.evidence_used) if run.grounding else None,
            evidence_available_count=evidence_available,
            grounding_json=(
                _bounded(run.grounding.model_dump(mode="json"), MAX_GROUNDING_JSON_CHARS)
                if run.grounding
                else None
            ),
            injection_flags_json=json.dumps(context.injection_flags) if context.injection_flags else None,
            error_message=run.error,
        )

        if run.report is None or run.grounding is None:
            return investigation

        # Findings, with their deterministic grounding verdict attached. Keyed
        # by text because that is what the grounding report carries back.
        verdicts = {f.finding_text: f for f in run.grounding.findings}

        for finding_type, findings in (
            (InvestigationFindingType.KEY_FINDING, run.report.key_findings),
            (InvestigationFindingType.SUPPORTING_EVIDENCE, run.report.supporting_evidence),
            (InvestigationFindingType.CONFLICTING_EVIDENCE, run.report.conflicting_evidence),
        ):
            for finding in findings:
                self._add_finding(investigation.id, finding, finding_type, verdicts)

        for rec in run.report.recommendations:
            self._investigations.add_recommendation(
                investigation_id=investigation.id,
                action=rec.action,
                rationale=rec.rationale,
                cited_evidence_ids_json=json.dumps(rec.evidence_ids),
            )

        return investigation

    def _add_finding(
        self,
        investigation_id: int,
        finding: ReportFinding,
        finding_type: InvestigationFindingType,
        verdicts: dict,
    ) -> None:
        verdict = verdicts.get(finding.finding)
        valid_ids = verdict.valid_evidence_ids if verdict else []
        invalid_ids = verdict.invalid_evidence_ids if verdict else []

        self._investigations.add_finding(
            investigation_id=investigation_id,
            # Only a VALID id may occupy the FK. An id the model invented has
            # no row to point at, so the write would fail on the constraint --
            # correctly. The full cited list (valid and invalid) is preserved
            # in cited_evidence_ids_json, so nothing is hidden.
            evidence_id=valid_ids[0] if valid_ids else None,
            finding_text=finding.finding,
            finding_type=finding_type,
            cited_evidence_ids_json=json.dumps(finding.evidence_ids),
            confidence_statement=finding.confidence_statement,
            grounding_status=verdict.status if verdict else GroundingStatus.UNCITED,
            invalid_evidence_ids_json=json.dumps(invalid_ids) if invalid_ids else None,
        )

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    def get(self, investigation_id: int) -> Investigation:
        investigation = self._investigations.get_by_id(investigation_id)
        if investigation is None:
            raise InvestigationNotFoundError(investigation_id)
        return investigation

    def list_for_client(self, external_client_id: int, *, limit: int = 50, offset: int = 0):
        client = self._clients.get_by_external_id(external_client_id)
        if client is None:
            raise ClientNotFoundError(external_client_id)
        return (
            client,
            self._investigations.list_for_client(client.id, limit=limit, offset=offset),
            self._investigations.count_for_client(client.id),
        )

    def agent_status(self) -> dict:
        return {
            "provider": self._agent.provider_name,
            "model": self._agent.model,
            "configured": self._agent.is_configured(),
            "prompt_version": PROMPT_VERSION,
        }


__all__ = [
    "ClientNotFoundError",
    "InvestigationNotFoundError",
    "InvestigationOrchestrator",
    "compute_context_hash",
]
