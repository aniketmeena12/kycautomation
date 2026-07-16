"""
CaseService -- the compliance workspace (Phase 6).

Aggregates, sequences, and records. It does NOT compute: no risk score, no
confidence, no entity resolution, no evidence writes. Everything it shows was
produced by Phases 2-5 and is read through a foreign key at request time (see
app/models/case.py for why nothing is copied onto the case).

THE ONE PLACE THIS SYSTEM MAY SET A HUMAN-ONLY STATE
-----------------------------------------------------
Phase 3 reserved `EntityMatchStatus.CONFIRMED` / `HUMAN_REVIEWED` for "a human
acting in a later phase" and guarded them at runtime (ADR-016). Phase 5
reserved `InvestigationStatus.ESCALATED` / `CLOSED` the same way (ADR-029).
This is that later phase -- and the authority comes from a named reviewer
supplying an action, never from a model and never from a schedule.
`apply_review` is the only writer of those states, and every one of them
produces a HumanReview row and an immutable audit record.

EVERY REVIEW WRITES THREE THINGS, ATOMICALLY
---------------------------------------------
  1. the state transition (validated first -- an illegal one raises and writes
     nothing),
  2. the HumanReview row (append-only; never an update),
  3. the AuditLog row (brief SS7/SS10: "Every review action produces an audit
     record").

Order matters: validate, then mutate, then record. A review recorded for a
transition that was rejected would be a lie in the audit trail.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.casework.sar import SARGenerator
from app.casework.schemas import CaseMetrics, CaseSummary, CaseTimeline
from app.casework.state_machine import (
    IllegalActionError,
    IllegalTransitionError,
    action_rule,
    available_actions,
    resolve_action,
)
from app.casework.timeline import TimelineBuilder
from app.core.config import Settings, get_settings
from app.core.enums import (
    ActorType,
    AlertStatus,
    CaseStatus,
    EntityMatchStatus,
    InvestigationStatus,
    ReviewAction,
    SARStatus,
)
from app.investigation.context import ContextBuilder
from app.models.case import Case
from app.models.review import HumanReview
from app.models.sar import SARDraft
from app.providers.llm_registry import get_llm_provider
from app.repositories.audit_repository import AuditLogRepository
from app.repositories.case_repository import CaseRepository
from app.repositories.client_repository import ClientRepository
from app.repositories.entity_match_repository import EntityMatchRepository
from app.repositories.investigation_repository import InvestigationRepository
from app.repositories.risk_repository import AlertRepository, RiskSnapshotRepository
from app.risk.config import get_risk_registry
from app.services.audit_service import record_audit_event

MAX_SAR_CONTENT_CHARS = 200_000


class CaseNotFoundError(Exception):
    def __init__(self, case_id: int) -> None:
        self.case_id = case_id
        super().__init__(f"No case with id={case_id}.")


class ClientNotFoundError(Exception):
    def __init__(self, external_client_id: int) -> None:
        super().__init__(f"No client with external_client_id={external_client_id}.")


class ReviewRejectedError(Exception):
    """An illegal action or transition. Surfaced as 409, never swallowed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CaseService:
    def __init__(
        self, db: Session, settings: Settings | None = None, sar_generator: SARGenerator | None = None
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._cases = CaseRepository(db)
        self._clients = ClientRepository(db)
        self._snapshots = RiskSnapshotRepository(db)
        self._alerts = AlertRepository(db)
        self._investigations = InvestigationRepository(db)
        self._matches = EntityMatchRepository(db)
        self._audit = AuditLogRepository(db)
        self._timeline = TimelineBuilder(db)
        self._sar_generator = sar_generator  # resolved lazily; see _generator()

    # ------------------------------------------------------------------ #
    # Open / read
    # ------------------------------------------------------------------ #

    def open_case_for_client(
        self,
        external_client_id: int,
        *,
        title: str | None = None,
        reason: str | None = None,
        alert_id: int | None = None,
        investigation_id: int | None = None,
        assigned_to: str | None = None,
        correlation_id: str | None = None,
    ) -> Case:
        """Idempotent per active case: a client already under review gets their
        existing case back, not a second one. Two open cases for one client is
        how two reviewers unknowingly work the same subject and reach different
        conclusions."""
        client = self._clients.get_by_external_id(external_client_id)
        if client is None:
            raise ClientNotFoundError(external_client_id)

        existing = self._cases.latest_open_for_client(client.id)
        if existing is not None:
            return existing

        case = self._cases.create(
            case_ref=self._cases.next_case_ref(),
            client_id=client.id,
            status=CaseStatus.OPEN,
            title=title or f"Case for {client.client_name}",
            opened_reason=reason,
            opening_alert_id=alert_id,
            opening_investigation_id=investigation_id,
            assigned_to=assigned_to,
            opened_at=_now(),
        )
        self._db.flush()

        record_audit_event(
            self._db,
            actor_type=ActorType.SYSTEM,
            actor_id="case_service",
            action="case_opened",
            target_type="Case",
            target_id=str(case.id),
            reason=reason or "Case opened.",
            new_value={
                "case_ref": case.case_ref,
                "status": case.status.value,
                "client_id": client.external_client_id,
            },
            correlation_id=correlation_id,
        )
        return case

    def get(self, case_id: int) -> Case:
        case = self._cases.get_by_id(case_id)
        if case is None:
            raise CaseNotFoundError(case_id)
        return case

    def list_cases(self, **kwargs) -> tuple[list[CaseSummary], int]:
        cases = self._cases.list(**kwargs)
        total = self._cases.count(status=kwargs.get("status"))
        return [self._summarize(c) for c in cases], total

    def _summarize(self, case: Case) -> CaseSummary:
        snapshot = self._snapshots.latest_for_client(case.client_id)
        open_alerts = [
            a for a in self._alerts.list(client_id=case.client_id, limit=200) if a.status == AlertStatus.OPEN
        ]
        return CaseSummary(
            id=case.id,
            case_ref=case.case_ref,
            client_id=case.client_id,
            external_client_id=case.client.external_client_id,
            client_name=case.client.client_name,
            status=case.status,
            title=case.title,
            assigned_to=case.assigned_to,
            opened_at=case.opened_at,
            closed_at=case.closed_at,
            current_risk_score=snapshot.current_score if snapshot else None,
            current_risk_band=snapshot.risk_band.value if snapshot else None,
            open_alert_count=len(open_alerts),
            investigation_count=self._investigations.count_for_client(case.client_id),
            review_count=len(case.reviews),
            has_sar_draft=bool(case.sar_drafts),
        )

    def timeline(self, case_id: int) -> CaseTimeline:
        return self._timeline.build(self.get(case_id))

    def audit_trail(self, case_id: int, *, limit: int = 200) -> list:
        """Every audit row touching this case or its client.

        Correlated by target rather than by a single id, because one case's
        story spans several target types -- the case itself, its client's
        monitoring cycles, and its investigations. An audit trail that showed
        only rows literally targeting `Case` would omit the reason the case
        exists.
        """
        case = self.get(case_id)
        external_id = str(case.client.external_client_id)
        investigation_ids = {
            str(i.id) for i in self._investigations.list_for_client(case.client_id, limit=200)
        }
        sar_ids = {str(s.id) for s in case.sar_drafts}

        from app.models.audit import AuditLog

        stmt = (
            select(AuditLog)
            .where(
                ((AuditLog.target_type == "Case") & (AuditLog.target_id == str(case.id)))
                | ((AuditLog.target_type == "Client") & (AuditLog.target_id == external_id))
                | (
                    (AuditLog.target_type == "Investigation")
                    & (AuditLog.target_id.in_(investigation_ids or {"__none__"}))
                )
                | ((AuditLog.target_type == "SARDraft") & (AuditLog.target_id.in_(sar_ids or {"__none__"})))
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt))

    def available_actions(self, case: Case) -> list[ReviewAction]:
        return available_actions(case.status)

    def action_requirements(self, case: Case) -> list[dict]:
        """The per-action contract behind `available_actions`.

        Read straight off the state machine's own rules. A caller that needs to
        know an action requires a target_id must never rederive that from a
        copied table -- the Phase 7 UI did exactly that, missed APPROVE and
        REJECT, and rendered a form the server rejected.
        """
        out = []
        for action in available_actions(case.status):
            rule = action_rule(action)
            out.append(
                {
                    "action": action.value,
                    "requires_target": rule.requires_target,
                    "target_type": rule.target_type,
                    "description": rule.description,
                }
            )
        return out

    # ------------------------------------------------------------------ #
    # Human review -- the only path to a human-only state
    # ------------------------------------------------------------------ #

    def apply_review(
        self,
        case_id: int,
        *,
        reviewer: str,
        action: ReviewAction,
        comment: str | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        correlation_id: str | None = None,
    ) -> HumanReview:
        case = self.get(case_id)
        previous = case.status

        # 1. VALIDATE FIRST. An illegal action must write nothing at all --
        #    not a review, not an audit row, not a partial transition.
        try:
            new_state = resolve_action(previous, action)
        except (IllegalActionError, IllegalTransitionError) as exc:
            raise ReviewRejectedError(str(exc)) from exc

        rule = action_rule(action)
        if rule.requires_target and target_id is None:
            raise ReviewRejectedError(
                f"Action {action.value} requires a target_id (the record being decided on)."
            )

        # 2. Apply the action's side effect on the record it targets.
        side_effect = self._apply_side_effect(case, action, target_id, reviewer, comment)

        # 3. Transition.
        case.status = new_state
        if new_state == CaseStatus.CLOSED:
            case.closed_at = _now()
            case.closed_reason = comment

        # 4. Record the review. Append-only.
        review = self._cases.add_review(
            case_id=case.id,
            investigation_id=case.opening_investigation_id,
            alert_id=case.opening_alert_id,
            reviewer_name=reviewer,
            action=action,
            rationale=comment,
            decided_at=_now(),
            previous_state=previous,
            new_state=new_state,
            target_type=target_type or side_effect.get("target_type"),
            target_id=target_id,
            correlation_id=correlation_id,
        )
        self._db.flush()

        # 5. Audit. ActorType.HUMAN -- a human decided this, and the trail must
        #    never let that be confused with SYSTEM or AGENT.
        record_audit_event(
            self._db,
            actor_type=ActorType.HUMAN,
            actor_id=reviewer,
            action=f"case_review:{action.value}",
            target_type="Case",
            target_id=str(case.id),
            reason=comment,
            old_value={"status": previous.value},
            new_value={
                "status": new_state.value,
                "action": action.value,
                "review_id": review.id,
                **side_effect,
            },
            correlation_id=correlation_id,
        )
        self._db.commit()
        return review

    def _apply_side_effect(
        self, case: Case, action: ReviewAction, target_id: int | None, reviewer: str, comment: str | None
    ) -> dict:
        """The record-level consequence of a review action."""
        if action in (ReviewAction.CONFIRM_MATCH, ReviewAction.REJECT_MATCH):
            return self._decide_match(case, action, target_id, reviewer)
        if action in (ReviewAction.APPROVE_DRAFT_SAR, ReviewAction.APPROVE):
            return self._decide_sar(case, target_id, SARStatus.APPROVED, reviewer)
        if action in (ReviewAction.REJECT_DRAFT_SAR, ReviewAction.REJECT):
            return self._decide_sar(case, target_id, SARStatus.REJECTED, reviewer)
        if action == ReviewAction.ESCALATE:
            return self._set_investigation_status(case, InvestigationStatus.ESCALATED)
        if action == ReviewAction.CLOSE_CASE:
            return self._set_investigation_status(case, InvestigationStatus.CLOSED)
        return {}

    def _decide_match(self, case: Case, action: ReviewAction, match_id: int | None, reviewer: str) -> dict:
        """A human confirming or rejecting an entity match.

        This is the ONLY code in the system that may write CONFIRMED /
        HUMAN_REVIEWED. Phase 3's engine is forbidden from it at runtime
        (ADR-016) precisely so that this moment -- a named person deciding --
        is the only way those states can ever appear on a row.
        """
        match = self._matches.get_by_id(match_id) if match_id is not None else None
        if match is None:
            raise ReviewRejectedError(f"EntityMatch {match_id} not found.")
        if match.subject_ref != f"client:{case.client.external_client_id}":
            # A reviewer on case A must not be able to adjudicate case B's
            # match by guessing an id.
            raise ReviewRejectedError(f"EntityMatch {match_id} does not belong to this case's client.")

        previous = match.status
        match.status = (
            EntityMatchStatus.CONFIRMED
            if action == ReviewAction.CONFIRM_MATCH
            else EntityMatchStatus.HUMAN_REVIEWED
        )
        self._db.flush()
        return {
            "target_type": "EntityMatch",
            "entity_match_id": match.id,
            "match_previous_status": previous.value,
            "match_new_status": match.status.value,
            "decided_by": reviewer,
        }

    def _decide_sar(self, case: Case, sar_id: int | None, status: SARStatus, reviewer: str) -> dict:
        sar = self._cases.get_sar(sar_id) if sar_id is not None else None
        if sar is None or sar.case_id != case.id:
            raise ReviewRejectedError(f"SAR draft {sar_id} not found on this case.")

        previous = sar.status
        sar.status = status
        sar.reviewed_by = reviewer
        sar.reviewed_at = _now()
        self._db.flush()

        record_audit_event(
            self._db,
            actor_type=ActorType.HUMAN,
            actor_id=reviewer,
            action=f"sar_{status.value.lower()}",
            target_type="SARDraft",
            target_id=str(sar.id),
            old_value={"status": previous.value},
            new_value={"status": status.value, "sar_ref": sar.sar_ref},
        )
        return {"target_type": "SARDraft", "sar_id": sar.id, "sar_status": status.value}

    def _set_investigation_status(self, case: Case, status: InvestigationStatus) -> dict:
        """Phase 5 left investigations at AWAITING_HUMAN_REVIEW and made
        ESCALATED/CLOSED unreachable from any automated path (ADR-029). A human
        review is the path."""
        investigations = self._investigations.list_for_client(case.client_id, limit=50)
        touched = []
        for investigation in investigations:
            if investigation.status == InvestigationStatus.AWAITING_HUMAN_REVIEW:
                investigation.status = status
                if status == InvestigationStatus.CLOSED:
                    investigation.closed_at = _now()
                touched.append(investigation.id)
        self._db.flush()
        return {"investigations_updated": touched, "investigation_status": status.value} if touched else {}

    # ------------------------------------------------------------------ #
    # SAR
    # ------------------------------------------------------------------ #

    def _generator(self) -> SARGenerator:
        if self._sar_generator is not None:
            return self._sar_generator
        try:
            provider = get_llm_provider(self._settings)
        except Exception:
            provider = None  # narrative unavailable; the SAR is still produced
        return SARGenerator(provider, max_output_tokens=min(self._settings.llm_max_output_tokens, 2000))

    def generate_sar(self, case_id: int, *, requested_by: str, correlation_id: str | None = None) -> SARDraft:
        """Generate a Draft SAR and move the case to SAR_REVIEW.

        Generating a draft is a SYSTEM action, not a human decision -- so it
        does not create a HumanReview. It does move the case into SAR_REVIEW,
        because that state means "a draft exists and is awaiting a human", which
        is exactly now true.
        """
        case = self.get(case_id)
        if case.status == CaseStatus.CLOSED:
            raise ReviewRejectedError("Cannot generate a SAR for a closed case.")

        context = ContextBuilder(self._db).build(
            case.client, trigger_reason=f"Draft SAR generation for {case.case_ref}"
        )
        timeline = self._timeline.build(case)

        latest = next(
            (i for i in self._investigations.list_for_client(case.client_id, limit=50) if i.report_json),
            None,
        )
        report = json.loads(latest.report_json) if latest and latest.report_json else None

        sar_ref = f"{case.case_ref}-SAR-{len(case.sar_drafts) + 1:02d}"
        document = self._generator().generate(
            sar_ref=sar_ref,
            context=context,
            timeline=timeline,
            investigation_report=report,
            case_ref=case.case_ref,
        )

        rendered = document.render()
        if len(rendered) > MAX_SAR_CONTENT_CHARS:
            rendered = rendered[:MAX_SAR_CONTENT_CHARS] + "\n[... truncated]"

        sar = self._cases.add_sar(
            client_id=case.client_id,
            case_id=case.id,
            investigation_id=latest.id if latest else None,
            # DRAFT. Nothing here can set APPROVED -- only a human review can.
            status=SARStatus.DRAFT,
            sar_ref=sar_ref,
            content=rendered,
            sections_json=json.dumps([s.model_dump(mode="json") for s in document.sections]),
            cited_evidence_ids_json=json.dumps(document.cited_evidence_ids),
            grounding_passed=document.grounding.passed if document.grounding else None,
            hallucinated_citation_count=(
                len(document.grounding.hallucinated_evidence_ids) if document.grounding else None
            ),
            narrative_generated_by=document.narrative_generated_by,
            narrative_model=document.narrative_model,
            prompt_version=document.prompt_version,
            narrative_error=document.narrative_error,
            generated_at=document.generated_at,
        )

        previous = case.status
        if case.status != CaseStatus.SAR_REVIEW:
            from app.casework.state_machine import validate_transition

            validate_transition(case.status, CaseStatus.SAR_REVIEW)
            case.status = CaseStatus.SAR_REVIEW
        self._db.flush()

        record_audit_event(
            self._db,
            actor_type=ActorType.SYSTEM,
            actor_id="sar_generator",
            action="sar_drafted",
            target_type="SARDraft",
            target_id=str(sar.id),
            reason=f"Draft SAR requested by {requested_by}.",
            old_value={"case_status": previous.value},
            new_value={
                "sar_ref": sar_ref,
                "case_status": case.status.value,
                "status": SARStatus.DRAFT.value,
                "narrative_generated_by": document.narrative_generated_by,
                "grounding_passed": document.grounding.passed if document.grounding else None,
                "cited_evidence_ids": document.cited_evidence_ids,
            },
            correlation_id=correlation_id,
        )
        self._db.commit()
        return sar

    def latest_sar(self, case_id: int) -> SARDraft | None:
        self.get(case_id)
        return self._cases.latest_sar_for_case(case_id)

    def list_sars(self, case_id: int) -> list[SARDraft]:
        self.get(case_id)
        return self._cases.list_sars_for_case(case_id)

    # ------------------------------------------------------------------ #
    # Metrics (brief SS8)
    # ------------------------------------------------------------------ #

    def metrics(self) -> CaseMetrics:
        from app.models.investigation import Investigation

        escalation_bands = set(get_risk_registry().alerts.escalation_bands)
        high_risk = 0
        for case in self._cases.list(limit=1000):
            if case.status == CaseStatus.CLOSED:
                continue
            snapshot = self._snapshots.latest_for_client(case.client_id)
            if snapshot and snapshot.risk_band in escalation_bands:
                high_risk += 1

        # Mean over investigations that PRODUCED a report. Including failures
        # would average in the latency of calls that returned nothing, making
        # a broken provider look fast.
        latency = self._db.scalar(
            select(func.avg(Investigation.latency_ms)).where(Investigation.report_json.isnot(None))
        )

        return CaseMetrics(
            open_cases=self._cases.count(status=CaseStatus.OPEN),
            under_review_cases=self._cases.count(status=CaseStatus.UNDER_REVIEW),
            escalated_cases=self._cases.count(status=CaseStatus.ESCALATED),
            sar_review_cases=self._cases.count(status=CaseStatus.SAR_REVIEW),
            closed_cases=self._cases.count(status=CaseStatus.CLOSED),
            total_cases=self._cases.count(),
            high_risk_cases=high_risk,
            sar_pending=self._cases.count_sars(status=SARStatus.DRAFT),
            sar_approved=self._cases.count_sars(status=SARStatus.APPROVED),
            sar_rejected=self._cases.count_sars(status=SARStatus.REJECTED),
            human_review_count=self._cases.count_reviews(),
            human_reviews_by_action=self._cases.review_counts_by_action(),
            # None, not 0.0, when nothing has run -- 0.0 would read as "instant".
            average_investigation_latency_ms=float(latency) if latency is not None else None,
            investigations_total=self._db.scalar(select(func.count()).select_from(Investigation)) or 0,
            investigations_failed=self._db.scalar(
                select(func.count()).select_from(Investigation).where(Investigation.report_json.is_(None))
            )
            or 0,
            generated_at=_now(),
        )


__all__ = ["CaseNotFoundError", "CaseService", "ClientNotFoundError", "ReviewRejectedError"]
