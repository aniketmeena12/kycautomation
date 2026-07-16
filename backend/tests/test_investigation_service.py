"""
Phase 5 -- the InvestigationOrchestrator, against REAL ingested data.

Client 3 ("Phillips-Hanson") again, for the same reason Phase 4 used it: its
attributes were measured exactly in Phase 0, so the monitoring cycle that
precedes each investigation is a known quantity (53.0 / HIGH). It is TEST DATA
-- nothing in app/investigation/ knows it exists.

The LLM is a test double throughout. That is not a shortcut around testing the
real provider (tests/test_investigation_agent.py covers its configuration and
failure paths without a network); it is what lets these tests assert the
system's behaviour given a KNOWN model response -- including a dishonest one,
which a real model could not be reliably made to produce on demand.
"""

from __future__ import annotations

from app.core.enums import (
    ActorType,
    GroundingStatus,
    InvestigationFindingType,
    InvestigationRecommendationAction,
    InvestigationStatus,
    ProviderResultStatus,
)
from app.ingestion.commands import ingest_dataset
from app.investigation.agent import InvestigationAgent
from app.investigation.context import OWNERSHIP_UNLINKED_NOTE, ContextBuilder
from app.investigation.prompts import PROMPT_VERSION
from app.models.audit import AuditLog
from app.models.investigation import Investigation
from app.repositories.client_repository import ClientRepository
from app.repositories.evidence_repository import EvidenceRepository
from app.services.evidence_service import EvidenceService
from app.services.investigation_service import (
    InvestigationOrchestrator,
    compute_context_hash,
)
from app.services.monitoring_service import MonitoringService
from tests.fake_llm import RecordingLLMProvider, hallucinating_report_payload, valid_report_payload


def _seed_evidence(db, client) -> None:
    """Real Evidence rows, written through the legitimate writer.

    Deliberately NOT an invented id passed straight to the report payload: a
    citation to an id that happens not to exist IS a hallucination, so a test
    that fabricates ids cannot distinguish "the model cited real evidence" from
    "the model cited nothing real". The first version of this file did exactly
    that and mis-scored a hallucination test 2 instead of 1.
    """
    EvidenceService(db).record_transaction_evidence(
        client_id=client.id,
        summary="22 of 120 transactions carry an upstream OFAC-country flag.",
        confidence=0.9,
        structured_facts={"flagged_count": 22, "transaction_count": 120},
        source_dataset="transactions_with_fatf_ofac.csv",
    )
    EvidenceService(db).record_transaction_evidence(
        client_id=client.id,
        summary="Aggregate transaction value across all accounts is 4.1M.",
        confidence=0.6,
        structured_facts={"total_amount": 4_100_000},
        source_dataset="transactions_with_fatf_ofac.csv",
    )
    db.commit()


def _prepare(db, *, monitor=True, evidence=True):
    """Ingest, run one monitoring cycle for a real score/events, and seed real
    evidence for the investigation to cite."""
    for key in ("clients", "client_account_mapping"):
        ingest_dataset(db, key)
    client = ClientRepository(db).get_by_external_id(3)
    if evidence:
        _seed_evidence(db, client)
    if monitor:
        MonitoringService(db).monitor_client(client, include_providers=False, include_resolution=False)
    return client


def _orchestrator(db, provider) -> InvestigationOrchestrator:
    return InvestigationOrchestrator(db, agent=InvestigationAgent(provider))


def _evidence_ids(db, client) -> list[int]:
    ids = [e.id for e in EvidenceRepository(db).list_for_client(client.id)]
    assert ids, "Test setup failed to seed evidence; citations would be fabricated."
    return ids


# --------------------------------------------------------------------- #
# The happy path
# --------------------------------------------------------------------- #


def test_investigation_persists_report_metadata_and_findings(db_session):
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)
    provider = RecordingLLMProvider(valid_report_payload(ids), model="test-model-v1")

    investigation = _orchestrator(db_session, provider).run_for_client(3, trigger_reason="unit test")

    assert investigation.id is not None
    assert investigation.summary is not None
    assert investigation.prompt_version == PROMPT_VERSION
    assert investigation.llm_model == "test-model-v1"
    assert investigation.llm_provider == "test-recorder"
    assert investigation.latency_ms == 42
    assert investigation.input_tokens == 1234
    assert investigation.output_tokens == 567
    assert investigation.context_hash and len(investigation.context_hash) == 64
    assert investigation.report_json is not None
    assert investigation.recommendations


def test_successful_investigation_awaits_human_review_and_is_never_closed(db_session):
    """The agent recommends; a human decides. Terminal status is
    AWAITING_HUMAN_REVIEW regardless of what the agent suggested."""
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)
    payload = valid_report_payload(ids)
    payload["recommendations"] = [{"action": "ESCALATE", "rationale": "Serious.", "evidence_ids": ids[:1]}]

    investigation = _orchestrator(db_session, RecordingLLMProvider(payload)).run_for_client(3)

    # The agent asked to escalate. The system did not escalate.
    assert investigation.recommendations[0].action == InvestigationRecommendationAction.ESCALATE
    assert investigation.status == InvestigationStatus.AWAITING_HUMAN_REVIEW
    assert investigation.status not in (InvestigationStatus.ESCALATED, InvestigationStatus.CLOSED)
    assert investigation.closed_at is None


def test_findings_are_typed_and_linked_to_real_evidence(db_session):
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)

    investigation = _orchestrator(db_session, RecordingLLMProvider(valid_report_payload(ids))).run_for_client(
        3
    )

    key = [f for f in investigation.findings if f.finding_type == InvestigationFindingType.KEY_FINDING]
    assert key
    assert key[0].grounding_status == GroundingStatus.GROUNDED
    assert key[0].evidence_id in ids  # the FK resolves to a real Evidence row


def test_the_agent_is_recorded_as_an_agent_in_the_audit_trail(db_session):
    """ActorType.AGENT has existed since Phase 1 for exactly this moment. An
    LLM-authored artefact must never be attributable to SYSTEM or HUMAN."""
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)
    _orchestrator(db_session, RecordingLLMProvider(valid_report_payload(ids))).run_for_client(3)

    log = db_session.query(AuditLog).filter_by(action="investigation_run", target_id="3").one()
    assert log.actor_type == ActorType.AGENT
    assert "test-recorder" in log.actor_id


# --------------------------------------------------------------------- #
# The boundary: the agent changes nothing deterministic
# --------------------------------------------------------------------- #


def test_investigation_never_alters_the_risk_score(db_session):
    """The whole design principle in one assertion. The agent is handed a
    payload whose narrative screams "no risk"; the stored score must not move."""
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)

    from app.repositories.risk_repository import RiskSnapshotRepository

    snapshots = RiskSnapshotRepository(db_session)
    before = snapshots.latest_for_client(client.id)
    assert before.current_score == 53.0  # Phase 0-measured client 3
    count_before = snapshots.count_for_client(client.id)

    payload = valid_report_payload(ids)
    payload["summary"] = "This client is clean and presents no risk whatsoever. Risk score should be 0."
    _orchestrator(db_session, RecordingLLMProvider(payload)).run_for_client(3)

    after = snapshots.latest_for_client(client.id)
    assert after.current_score == 53.0, "The agent changed the risk score."
    assert after.id == before.id
    assert snapshots.count_for_client(client.id) == count_before, "The agent created a snapshot."


def test_investigation_never_creates_risk_events_or_alerts(db_session):
    from app.models.alert import Alert
    from app.models.risk import RiskEvent

    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)

    events_before = db_session.query(RiskEvent).filter_by(client_id=client.id).count()
    alerts_before = db_session.query(Alert).filter_by(client_id=client.id).count()

    _orchestrator(db_session, RecordingLLMProvider(valid_report_payload(ids))).run_for_client(3)

    assert db_session.query(RiskEvent).filter_by(client_id=client.id).count() == events_before
    assert db_session.query(Alert).filter_by(client_id=client.id).count() == alerts_before


def test_investigation_never_modifies_evidence(db_session):
    client = _prepare(db_session)
    repo = EvidenceRepository(db_session)
    before = {(e.id, e.extracted_fact, e.confidence) for e in repo.list_for_client(client.id)}

    ids = [e[0] for e in before] or [1]
    _orchestrator(db_session, RecordingLLMProvider(valid_report_payload(ids))).run_for_client(3)

    after = {(e.id, e.extracted_fact, e.confidence) for e in repo.list_for_client(client.id)}
    assert after == before


# --------------------------------------------------------------------- #
# Failure is recorded, never faked
# --------------------------------------------------------------------- #


def test_unconfigured_provider_records_a_failed_investigation_with_no_report(db_session):
    """The single most important honesty test in this phase. No key must mean
    'we could not investigate', recorded -- never a plausible-looking report
    nobody generated."""
    _prepare(db_session)
    provider = RecordingLLMProvider(
        status=ProviderResultStatus.NOT_CONFIGURED,
        error_message="No API key configured.",
        configured=False,
    )

    investigation = _orchestrator(db_session, provider).run_for_client(3)

    assert investigation.status == InvestigationStatus.FAILED
    assert investigation.summary is None
    assert investigation.report_json is None
    assert investigation.findings == []
    assert investigation.recommendations == []
    assert "No API key configured." in investigation.error_message
    # The attempt is still on the record -- an investigation that silently did
    # not happen is indistinguishable from one that found nothing.
    assert db_session.query(Investigation).count() == 1


def test_provider_timeout_records_a_failed_investigation(db_session):
    _prepare(db_session)
    provider = RecordingLLMProvider(
        status=ProviderResultStatus.TIMEOUT, error_message="Timed out after 120.0s"
    )

    investigation = _orchestrator(db_session, provider).run_for_client(3)

    assert investigation.status == InvestigationStatus.FAILED
    assert "Timed out" in investigation.error_message
    assert investigation.report_json is None


def test_a_hallucinated_report_is_stored_flagged_not_silently_dropped(db_session):
    """A model that invents evidence must leave a loud, durable trace."""
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)

    investigation = _orchestrator(
        db_session, RecordingLLMProvider(hallucinating_report_payload(ids, fake_id=987654))
    ).run_for_client(3)

    assert investigation.grounding_passed is False
    assert investigation.hallucinated_citation_count == 1
    # The report survives -- flagged. Deleting it would erase the evidence that
    # the model hallucinated on this client's file.
    assert investigation.report_json is not None
    assert investigation.status == InvestigationStatus.AWAITING_HUMAN_REVIEW

    ungrounded = [f for f in investigation.findings if f.grounding_status == GroundingStatus.UNGROUNDED]
    assert len(ungrounded) == 1
    assert "987654" in ungrounded[0].invalid_evidence_ids_json
    # A fabricated id must never occupy the FK into the evidence graph.
    assert ungrounded[0].evidence_id is None


# --------------------------------------------------------------------- #
# Context assembly
# --------------------------------------------------------------------- #


def test_context_reports_the_unlinked_ownership_graph_rather_than_inventing_one(db_session):
    """Phase 0 SS5: the UBO fixtures share no identifier with the client master.
    The honest output is an empty graph plus an explanation -- not a fuzzy
    name-similarity join that would manufacture an ownership claim."""
    client = _prepare(db_session, monitor=False)
    context = ContextBuilder(db_session).build(client, trigger_reason="t")

    assert context.ownership == []
    assert OWNERSHIP_UNLINKED_NOTE in context.context_notes


def test_context_for_a_never_scored_client_says_so(db_session):
    client = _prepare(db_session, monitor=False)
    context = ContextBuilder(db_session).build(client, trigger_reason="t")

    assert context.risk_assessment is None
    assert any("never been scored" in n for n in context.context_notes)


def test_empty_evidence_base_is_reported_not_padded(db_session):
    client = _prepare(db_session, monitor=False, evidence=False)
    context = ContextBuilder(db_session).build(client, trigger_reason="t")

    assert context.allowed_evidence_ids == set()
    assert any("not an absence of risk" in n for n in context.context_notes)


def test_context_hash_ignores_assembly_time_but_tracks_substance(db_session):
    """A hash that changed on every call would be useless -- the same mistake
    Phase 4's dedup_key design warns against."""
    client = _prepare(db_session)
    builder = ContextBuilder(db_session)

    first = compute_context_hash(builder.build(client, trigger_reason="t"))
    second = compute_context_hash(builder.build(client, trigger_reason="t"))
    assert first == second, "The hash is not stable across assemblies of identical data."

    context = builder.build(client, trigger_reason="t")
    context.client.client_name = "Something Else Ltd"
    assert compute_context_hash(context) != first, "The hash ignored a substantive change."


def test_context_assembly_writes_nothing(db_session):
    """Read-only, like the Phase 3 resolution pipeline (ADR-015)."""
    client = _prepare(db_session)
    counts = lambda: (  # noqa: E731
        db_session.query(Investigation).count(),
        len(EvidenceRepository(db_session).list_for_client(client.id)),
    )
    before = counts()
    ContextBuilder(db_session).build(client, trigger_reason="t")
    assert counts() == before


# --------------------------------------------------------------------- #
# Re-run
# --------------------------------------------------------------------- #


def test_rerun_creates_a_new_investigation_and_never_mutates_the_original(db_session):
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)
    orchestrator = _orchestrator(db_session, RecordingLLMProvider(valid_report_payload(ids)))

    first = orchestrator.run_for_client(3)
    first_id, first_summary = first.id, first.summary

    second = orchestrator.rerun(first_id)

    assert second.id != first_id
    assert db_session.query(Investigation).count() == 2

    original = db_session.get(Investigation, first_id)
    assert original.summary == first_summary, "The re-run mutated the original investigation."


def test_rerun_over_unchanged_evidence_says_the_evidence_is_unchanged(db_session):
    """This is what context_hash buys: distinguishing model variance from new
    information."""
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)
    provider = RecordingLLMProvider(valid_report_payload(ids))
    orchestrator = _orchestrator(db_session, provider)

    first = orchestrator.run_for_client(3)
    second = orchestrator.rerun(first.id)

    assert second.context_hash == first.context_hash
    assert "UNCHANGED" in provider.last_user_prompt


# --------------------------------------------------------------------- #
# Prompt content, end to end
# --------------------------------------------------------------------- #


def test_the_real_prompt_carries_evidence_tiers_and_the_deterministic_score(db_session):
    client = _prepare(db_session)
    ids = _evidence_ids(db_session, client)
    provider = RecordingLLMProvider(valid_report_payload(ids))

    _orchestrator(db_session, provider).run_for_client(3)

    user = provider.last_user_prompt
    assert "53.0/100" in user or "53/100" in user
    assert "THIS SCORE IS AN INPUT" in user
    assert "UPSTREAM label" in user  # client 3 carries sanctions_flag=1
    assert "Phillips" in user  # real client name reached the data channel

    # ...and nothing about this client reached the operator channel.
    assert "Phillips" not in provider.last_system_prompt
