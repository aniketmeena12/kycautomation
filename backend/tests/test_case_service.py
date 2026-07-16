"""
Phase 6 -- CaseService, TimelineBuilder, and the SAR generator, against REAL
ingested data.

Client 3 ("Phillips-Hanson") again, for the reason Phases 4 and 5 used it: its
attributes were measured in Phase 0, so the monitoring cycle underneath every
case here is a known quantity (53.0 / HIGH). It is TEST DATA -- nothing in
app/casework/ knows it exists.
"""

from __future__ import annotations

import json

import pytest

from app.casework.sar import DRAFT_MARKING, SARGenerator
from app.casework.timeline import TimelineBuilder
from app.core.enums import (
    ActorType,
    CaseStatus,
    EntityMatchStatus,
    InvestigationStatus,
    ReviewAction,
    SARStatus,
    TimelineEntryType,
)
from app.ingestion.commands import ingest_dataset
from app.investigation.agent import InvestigationAgent
from app.models.audit import AuditLog
from app.repositories.client_repository import ClientRepository
from app.services.case_service import CaseService, ReviewRejectedError
from app.services.evidence_service import EvidenceService
from app.services.investigation_service import InvestigationOrchestrator
from app.services.monitoring_service import MonitoringService
from tests.fake_llm import RecordingLLMProvider, valid_report_payload

REVIEWER = "alice.compliance"


def _prepare(db, *, investigate=True):
    for key in ("clients", "client_account_mapping"):
        ingest_dataset(db, key)
    client = ClientRepository(db).get_by_external_id(3)

    EvidenceService(db).record_transaction_evidence(
        client_id=client.id,
        summary="22 of 120 transactions carry an upstream OFAC-country flag.",
        confidence=0.9,
        structured_facts={"flagged_count": 22, "transaction_count": 120},
        source_dataset="transactions_with_fatf_ofac.csv",
    )
    db.commit()

    MonitoringService(db).monitor_client(client, include_providers=False, include_resolution=False)

    if investigate:
        ids = [e.id for e in EvidenceService(db).list_for_client(client.id)]
        InvestigationOrchestrator(
            db, agent=InvestigationAgent(RecordingLLMProvider(valid_report_payload(ids)))
        ).run_for_client(3)
    return client


def _service(db) -> CaseService:
    return CaseService(db)


def _open(db) -> "object":
    case = _service(db).open_case_for_client(3, reason="Phase 6 test")
    db.commit()
    return case


# --------------------------------------------------------------------- #
# Case lifecycle
# --------------------------------------------------------------------- #


def test_opening_a_case_records_an_audit_entry(db_session):
    _prepare(db_session)
    case = _open(db_session)

    assert case.status == CaseStatus.OPEN
    assert case.case_ref.startswith("CASE-")
    log = db_session.query(AuditLog).filter_by(action="case_opened", target_id=str(case.id)).one()
    assert log.actor_type == ActorType.SYSTEM


def test_opening_a_case_twice_returns_the_same_active_case(db_session):
    """Two open cases for one client is how two reviewers unknowingly work the
    same subject and reach different conclusions."""
    _prepare(db_session)
    first = _open(db_session)
    second = _service(db_session).open_case_for_client(3)
    assert second.id == first.id


def test_a_closed_case_does_not_block_a_new_one(db_session):
    """A client legitimately has a history of cases."""
    _prepare(db_session)
    first = _open(db_session)
    _service(db_session).apply_review(
        first.id, reviewer=REVIEWER, action=ReviewAction.CLOSE_CASE, comment="done"
    )

    second = _service(db_session).open_case_for_client(3)
    db_session.commit()
    assert second.id != first.id
    assert second.status == CaseStatus.OPEN


def test_case_aggregates_the_client_without_copying_it(db_session):
    """The Case row stores lifecycle only -- no score, no summary. A workspace
    showing a stale score next to a live investigation is worse than none."""
    from app.models.case import Case

    _prepare(db_session)
    _open(db_session)
    columns = set(Case.__table__.columns.keys())

    for leaked in ("risk_score", "risk_band", "summary", "evidence", "investigation_summary"):
        assert leaked not in columns, f"Case duplicates {leaked!r} -- a second source of truth."


# --------------------------------------------------------------------- #
# Human review workflow
# --------------------------------------------------------------------- #


def test_review_records_reviewer_action_states_and_audit(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)

    review = service.apply_review(
        case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING, comment="Nothing new."
    )

    assert review.reviewer_name == REVIEWER
    assert review.action == ReviewAction.CONTINUE_MONITORING
    assert review.rationale == "Nothing new."
    assert review.previous_state == CaseStatus.OPEN
    assert review.new_state == CaseStatus.UNDER_REVIEW
    assert review.decided_at is not None
    assert service.get(case.id).status == CaseStatus.UNDER_REVIEW

    # brief SS10: every review action produces an audit record.
    log = (
        db_session.query(AuditLog)
        .filter_by(action="case_review:CONTINUE_MONITORING", target_id=str(case.id))
        .one()
    )
    assert log.actor_type == ActorType.HUMAN
    assert log.actor_id == REVIEWER
    assert json.loads(log.old_value)["status"] == "OPEN"
    assert json.loads(log.new_value)["status"] == "UNDER_REVIEW"


def test_reviews_are_append_only(db_session):
    """A reviewer who changes their mind records a NEW review. 'Escalated, then
    closed an hour later' and 'closed' are different facts."""
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)

    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.ESCALATE, comment="worrying")
    service.apply_review(
        case.id, reviewer="bob.senior", action=ReviewAction.CLOSE_CASE, comment="false positive"
    )

    reviews = service.get(case.id).reviews
    assert len(reviews) == 2
    assert [r.action for r in reviews] == [ReviewAction.ESCALATE, ReviewAction.CLOSE_CASE]
    # The first review is untouched -- its recorded transition still says what
    # it did at the time.
    assert reviews[0].new_state == CaseStatus.ESCALATED


def test_repository_exposes_no_way_to_update_or_delete_a_review(db_session):
    """ "Never overwrite reviews" is a write path that does not exist."""
    from app.repositories.case_repository import CaseRepository

    methods = {m for m in dir(CaseRepository) if not m.startswith("_")}
    for banned in ("update_review", "delete_review", "edit_review", "remove_review"):
        assert banned not in methods


def test_an_illegal_action_writes_nothing_at_all(db_session):
    """Validate, then mutate, then record. A review stored for a transition that
    was rejected would be a lie in the audit trail."""
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CLOSE_CASE, comment="done")

    reviews_before = len(service.get(case.id).reviews)
    audits_before = db_session.query(AuditLog).count()

    with pytest.raises(ReviewRejectedError):
        service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.ESCALATE)

    assert len(service.get(case.id).reviews) == reviews_before
    assert db_session.query(AuditLog).count() == audits_before
    assert service.get(case.id).status == CaseStatus.CLOSED  # untouched


def test_close_case_records_who_closed_it_and_why(db_session):
    _prepare(db_session)
    case = _open(db_session)
    _service(db_session).apply_review(
        case.id, reviewer=REVIEWER, action=ReviewAction.CLOSE_CASE, comment="Reviewed; benign."
    )

    closed = _service(db_session).get(case.id)
    assert closed.status == CaseStatus.CLOSED
    assert closed.closed_at is not None
    assert closed.closed_reason == "Reviewed; benign."


# --------------------------------------------------------------------- #
# The human-only states Phases 3 and 5 reserved
# --------------------------------------------------------------------- #


def test_confirm_match_is_the_only_route_to_a_human_only_status(db_session):
    """ADR-016 forbade the engine from ever writing CONFIRMED. This is the
    'later phase' it was reserved for -- and the authority is a named person."""
    from app.repositories.entity_match_repository import EntityMatchRepository

    client = _prepare(db_session)
    match, _ = EntityMatchRepository(db_session).upsert(
        subject_type="CLIENT",
        subject_id=client.id,
        subject_ref=f"client:{client.external_client_id}",
        candidate_provider="local_sanctions",
        candidate_external_id="X-1",
        candidate_name="PHILLIPS HANSON HOLDINGS",
        name_similarity_score=88.0,
        combined_confidence=88.0,
        status=EntityMatchStatus.HIGH_CONFIDENCE,
    )
    db_session.commit()
    case = _open(db_session)

    _service(db_session).apply_review(
        case.id,
        reviewer=REVIEWER,
        action=ReviewAction.CONFIRM_MATCH,
        comment="Verified against passport.",
        target_id=match.id,
    )

    db_session.refresh(match)
    assert match.status == EntityMatchStatus.CONFIRMED

    log = db_session.query(AuditLog).filter_by(action="case_review:CONFIRM_MATCH").one()
    assert log.actor_type == ActorType.HUMAN
    assert json.loads(log.new_value)["match_new_status"] == "CONFIRMED"


def test_confirm_match_requires_a_target(db_session):
    _prepare(db_session)
    case = _open(db_session)
    with pytest.raises(ReviewRejectedError, match="requires a target_id"):
        _service(db_session).apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONFIRM_MATCH)


def test_a_reviewer_cannot_adjudicate_another_clients_match(db_session):
    """Guessing an id must not let a reviewer on case A decide case B."""
    from app.repositories.entity_match_repository import EntityMatchRepository

    _prepare(db_session)
    other = ClientRepository(db_session).get_by_external_id(4)
    match, _ = EntityMatchRepository(db_session).upsert(
        subject_type="CLIENT",
        subject_id=other.id,
        subject_ref=f"client:{other.external_client_id}",
        candidate_provider="local_sanctions",
        candidate_external_id="X-9",
        candidate_name="SOMEONE ELSE",
        name_similarity_score=80.0,
        combined_confidence=80.0,
        status=EntityMatchStatus.POSSIBLE,
    )
    db_session.commit()
    case = _open(db_session)  # case is for client 3

    with pytest.raises(ReviewRejectedError, match="does not belong"):
        _service(db_session).apply_review(
            case.id, reviewer=REVIEWER, action=ReviewAction.CONFIRM_MATCH, target_id=match.id
        )


def test_escalating_a_case_escalates_its_investigation(db_session):
    """Phase 5 left investigations at AWAITING_HUMAN_REVIEW with no automated
    path to ESCALATED (ADR-029). A human review is that path."""
    _prepare(db_session)
    case = _open(db_session)
    _service(db_session).apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.ESCALATE)

    from app.repositories.investigation_repository import InvestigationRepository

    investigations = InvestigationRepository(db_session).list_for_client(case.client_id)
    assert investigations
    assert investigations[0].status == InvestigationStatus.ESCALATED


# --------------------------------------------------------------------- #
# Timeline (brief SS3, SS10)
# --------------------------------------------------------------------- #


def test_timeline_is_generated_from_stored_rows(db_session):
    _prepare(db_session)
    case = _open(db_session)
    timeline = TimelineBuilder(db_session).build(case)

    assert timeline.entries
    types = {e.entry_type for e in timeline.entries}
    assert TimelineEntryType.MONITORING in types
    assert TimelineEntryType.RISK_EVENT in types
    assert TimelineEntryType.EVIDENCE in types
    assert TimelineEntryType.INVESTIGATION in types

    # Every entry points back at the row it was derived from.
    for entry in timeline.entries:
        assert entry.source_table and entry.source_id
        assert entry.entry_key == f"{entry.entry_type.value}:{entry.source_id}"


def test_timeline_is_chronological_and_deterministic(db_session):
    """Rows sharing a timestamp are routine -- a monitoring cycle writes a
    snapshot and several events in the same instant. Without the tiebreaker,
    two reads of the same case would disagree."""
    _prepare(db_session)
    case = _open(db_session)
    builder = TimelineBuilder(db_session)

    first = builder.build(case)
    timestamps = [e.timestamp for e in first.entries]
    assert timestamps == sorted(timestamps)

    second = builder.build(case)
    assert [e.entry_key for e in first.entries] == [e.entry_key for e in second.entries]


def test_timeline_has_no_duplicate_entries(db_session):
    _prepare(db_session)
    case = _open(db_session)
    entries = TimelineBuilder(db_session).build(case).entries
    keys = [e.entry_key for e in entries]
    assert len(keys) == len(set(keys))


def test_timeline_separates_system_agent_and_human_actors(db_session):
    """An LLM's opinion and a compliance officer's decision must never look
    alike in a timeline."""
    _prepare(db_session)
    case = _open(db_session)
    _service(db_session).apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.ESCALATE, comment="x")

    entries = TimelineBuilder(db_session).build(_service(db_session).get(case.id)).entries
    by_actor = {e.actor_type for e in entries}
    assert ActorType.SYSTEM in by_actor
    assert ActorType.AGENT in by_actor  # the investigation
    assert ActorType.HUMAN in by_actor  # the review

    investigation = next(e for e in entries if e.entry_type == TimelineEntryType.INVESTIGATION)
    assert investigation.actor_type == ActorType.AGENT

    review = next(e for e in entries if e.entry_type == TimelineEntryType.HUMAN_REVIEW)
    assert review.actor_type == ActorType.HUMAN
    assert review.actor_id == REVIEWER


def test_timeline_carries_evidence_tier(db_session):
    """ADR-002: a curated demo hit must never be presentable as authoritative --
    and a timeline is a presentation."""
    _prepare(db_session)
    case = _open(db_session)
    evidence = [
        e
        for e in TimelineBuilder(db_session).build(case).entries
        if e.entry_type == TimelineEntryType.EVIDENCE
    ]
    assert evidence
    assert all("source_tier" in e.metadata for e in evidence)


def test_timeline_builder_writes_nothing(db_session):
    _prepare(db_session)
    case = _open(db_session)
    before = db_session.query(AuditLog).count()
    TimelineBuilder(db_session).build(case)
    assert db_session.query(AuditLog).count() == before


def test_timeline_has_no_public_append_method():
    """A timeline you can append to is one someone can append to incorrectly."""
    methods = {m for m in dir(TimelineBuilder) if not m.startswith("_")}
    assert methods == {"build"}


# --------------------------------------------------------------------- #
# SAR (brief SS6, SS10)
# --------------------------------------------------------------------- #


def test_sar_is_generated_marked_draft_and_moves_the_case_to_sar_review(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)

    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    assert sar.status == SARStatus.DRAFT
    assert sar.sar_ref
    assert DRAFT_MARKING in sar.content
    assert (
        "Requires Human Approval".lower() in sar.content.lower() or "REQUIRES HUMAN APPROVAL" in sar.content
    )
    assert service.get(case.id).status == CaseStatus.SAR_REVIEW


def test_sar_contains_all_nine_sections(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)
    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    keys = [s["key"] for s in json.loads(sar.sections_json)]
    assert keys == [
        "subject_information",
        "executive_summary",
        "chronology",
        "risk_indicators",
        "supporting_evidence",
        "investigation_findings",
        "recommendations",
        "reviewer_notes",
        "disclaimer",
    ]


def test_only_the_narrative_is_llm_generated(db_session):
    """Eight of nine sections are deterministic. The model cannot add a row to
    Chronology or Supporting Evidence -- those were finished before it was
    called."""
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)
    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    sections = json.loads(sar.sections_json)
    for section in sections:
        if section["key"] == "executive_summary":
            continue
        assert (
            section["generated_by"] == "deterministic"
        ), f"Section {section['key']} was not deterministic -- the LLM must only write narrative."


def test_sar_references_evidence_ids(db_session):
    """brief SS10: every SAR references evidence IDs."""
    client = _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)
    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    real_ids = {e.id for e in EvidenceService(db_session).list_for_client(client.id)}
    cited = set(json.loads(sar.cited_evidence_ids_json))
    assert cited
    assert cited <= real_ids, "The SAR cites evidence that does not exist."


def test_sar_is_generated_even_without_an_llm(db_session):
    """A SAR is a factual document whose facts are deterministic. The absence of
    a model must never be why a compliance officer has no draft to read."""
    _prepare(db_session)
    case = _open(db_session)
    service = CaseService(db_session, sar_generator=SARGenerator(provider=None))
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)

    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    assert sar.status == SARStatus.DRAFT
    assert sar.narrative_error and "no LLM provider" in sar.narrative_error
    sections = {s["key"]: s for s in json.loads(sar.sections_json)}
    assert sections["executive_summary"]["generated_by"] == "unavailable"
    assert "could not be generated" in sections["executive_summary"]["body"]
    # The factual sections are intact.
    assert sections["risk_indicators"]["generated_by"] == "deterministic"
    assert "53" in sections["risk_indicators"]["body"]


def test_a_hallucinated_sar_narrative_is_flagged_in_the_document(db_session):
    """A reviewer reading the SAR must see this without opening the database."""
    from app.core.enums import ProviderResultStatus
    from app.providers.llm_contracts import LLMInvocationResult

    class Hallucinating:
        provider_name = "test"
        model = "test-model"

        def is_configured(self):
            return True

        def complete_json(self, **kwargs):
            return LLMInvocationResult(
                status=ProviderResultStatus.SUCCESS,
                provider=self.provider_name,
                model=self.model,
                parsed={
                    "executive_summary": "As shown in evidence 987654, the subject...",
                    "cited_evidence_ids": [987654],
                },
            )

    _prepare(db_session)
    case = _open(db_session)
    service = CaseService(db_session, sar_generator=SARGenerator(Hallucinating()))
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)

    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    assert sar.grounding_passed is False
    assert sar.hallucinated_citation_count == 1
    sections = {s["key"]: s for s in json.loads(sar.sections_json)}
    assert "WARNING" in sections["executive_summary"]["body"]
    assert "987654" in sections["executive_summary"]["body"]


def test_reviewer_notes_are_never_machine_populated(db_session):
    """A system that pre-filled them would be putting words in the mouth of the
    person accountable for the filing."""
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)
    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    notes = next(s for s in json.loads(sar.sections_json) if s["key"] == "reviewer_notes")
    assert "intentionally blank" in notes["body"]
    assert notes["generated_by"] == "deterministic"


def test_nothing_automated_can_approve_a_sar(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)
    sar = service.generate_sar(case.id, requested_by=REVIEWER)
    assert sar.status == SARStatus.DRAFT

    # Only a human review can.
    service.apply_review(
        case.id, reviewer="bob.senior", action=ReviewAction.APPROVE_DRAFT_SAR, target_id=sar.id, comment="ok"
    )
    db_session.refresh(sar)
    assert sar.status == SARStatus.APPROVED
    assert sar.reviewed_by == "bob.senior"
    # ...and approving does NOT close the case.
    assert service.get(case.id).status == CaseStatus.SAR_REVIEW


def test_rejecting_a_sar_returns_the_case_for_more_work(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)
    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    service.apply_review(
        case.id, reviewer=REVIEWER, action=ReviewAction.REJECT_DRAFT_SAR, target_id=sar.id, comment="thin"
    )
    db_session.refresh(sar)
    assert sar.status == SARStatus.REJECTED
    assert service.get(case.id).status == CaseStatus.UNDER_REVIEW


def test_sar_cannot_be_generated_for_a_closed_case(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CLOSE_CASE, comment="done")

    with pytest.raises(ReviewRejectedError, match="closed case"):
        service.generate_sar(case.id, requested_by=REVIEWER)


def test_sar_generation_is_audited(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)
    sar = service.generate_sar(case.id, requested_by=REVIEWER)

    log = db_session.query(AuditLog).filter_by(action="sar_drafted", target_id=str(sar.id)).one()
    assert log.actor_type == ActorType.SYSTEM


# --------------------------------------------------------------------- #
# Audit trail + metrics
# --------------------------------------------------------------------- #


def test_audit_trail_spans_the_cases_whole_story(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.ESCALATE, comment="x")

    actions = {e.action for e in service.audit_trail(case.id)}
    assert "case_opened" in actions
    assert "case_review:ESCALATE" in actions
    assert "monitoring_cycle" in actions  # from the client's monitoring
    assert "investigation_run" in actions  # from Phase 5


def test_audit_repository_cannot_update_or_delete(db_session):
    from app.repositories.audit_repository import AuditLogRepository

    methods = {m for m in dir(AuditLogRepository) if not m.startswith("_")}
    assert "create" in methods
    for banned in ("update", "delete", "remove", "purge"):
        assert banned not in methods


def test_metrics_report_counts_not_quality(db_session):
    _prepare(db_session)
    case = _open(db_session)
    service = _service(db_session)
    service.apply_review(case.id, reviewer=REVIEWER, action=ReviewAction.CONTINUE_MONITORING)

    metrics = service.metrics()
    assert metrics.total_cases == 1
    assert metrics.under_review_cases == 1
    assert metrics.human_review_count == 1
    assert metrics.human_reviews_by_action["CONTINUE_MONITORING"] == 1
    assert metrics.high_risk_cases == 1  # client 3 is HIGH
    assert metrics.investigations_total == 1
    assert metrics.average_investigation_latency_ms == 42.0


def test_latency_is_null_not_zero_when_nothing_ran(db_session):
    """0.0 would read as 'instant'."""
    _prepare(db_session, investigate=False)
    _open(db_session)
    assert _service(db_session).metrics().average_investigation_latency_ms is None
