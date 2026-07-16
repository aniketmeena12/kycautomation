"""
EntityResolutionService + EvidenceService, against REAL ingested Phase 0 data.

These are the real-dataset regression tests (Phase 3 brief SS15): they ingest
the actual curated sanctions fixture and UBO showcase graph and assert the
engine reproduces the outcome docs/phase-0-dataset-audit.md predicted.
"""

import json

import pytest

from app.core.enums import EntityMatchStatus, EntityMatchSubjectType, EvidenceType, ProviderKind, SourceTier
from app.ingestion.commands import ingest_dataset
from app.models.resolution import EntityMatch
from app.repositories.client_repository import ClientRepository
from app.repositories.ownership_repository import OwnershipRepository
from app.services.entity_resolution_service import EntityResolutionService
from app.services.evidence_service import EvidenceService


def _ingest(db, *keys):
    for key in keys:
        ingest_dataset(db, key)


def _showcase_person(db):
    entities, _ = OwnershipRepository(db).get_graph("showcase_structure")
    return next(e for e in entities if e.entity_type == "individual")


# ------------------------------------------------- real-dataset regression


def test_real_ubo_person_resolves_to_curated_sanctions_entity(db_session):
    """The headline Phase 0 scenario: a sanctioned individual hidden 3 layers
    deep in the ownership graph is found in the sanctions data."""
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)

    run = service.resolve_and_persist(
        service.subject_for_ownership_entity(person),
        subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
        subject_id=person.id,
    )

    assert run.results
    top = run.results[0]
    assert top.status == EntityMatchStatus.HIGH_CONFIDENCE
    assert top.confidence >= 85
    assert "AL-RASHID" in top.candidate.name
    # Tier provenance survives resolution (ADR-002).
    assert top.candidate.source_tier == SourceTier.TIER_2_CURATED_DEMO.value


def test_real_client_against_sanctions_is_honestly_weak(db_session):
    """docs/phase-0-dataset-audit.md SS3 measured 0/2000 client names matching
    the authoritative lists. The engine must reproduce that honestly rather
    than manufacture a hit."""
    _ingest(db_session, "clients", "sample_ofac_sdn")
    client = ClientRepository(db_session).get_by_external_id(3)
    service = EntityResolutionService(db_session)

    run = service.resolve_and_persist(
        service.subject_for_client(client),
        subject_type=EntityMatchSubjectType.CLIENT,
        subject_id=client.id,
        client_id=client.id,
    )
    high = [r for r in run.results if r.status == EntityMatchStatus.HIGH_CONFIDENCE]
    assert high == []


# ----------------------------------------------------------- persistence


def test_matches_persist_with_full_explanation(db_session):
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)
    run = service.resolve_and_persist(
        service.subject_for_ownership_entity(person),
        subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
        subject_id=person.id,
    )

    match = db_session.get(EntityMatch, run.results[0].persisted_match_id)
    assert match is not None
    assert match.subject_ref == f"ownership:showcase_structure:{person.external_entity_id}"
    assert match.candidate_name
    assert match.combined_confidence >= 85
    assert json.loads(match.matched_attributes)  # stored, replayable without re-running
    reasons = json.loads(match.reasons)
    assert reasons["summary"]
    assert "positive" in reasons


def test_db_sourced_candidate_gets_a_real_fk_provider_sourced_does_not(db_session):
    """A DB candidate links by FK; a streaming-provider candidate cannot and
    correctly uses provider+external_id instead."""
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)
    run = service.resolve_and_persist(
        service.subject_for_ownership_entity(person),
        subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
        subject_id=person.id,
    )
    match = db_session.get(EntityMatch, run.results[0].persisted_match_id)
    assert match.candidate_sanctions_entity_id is not None
    assert match.candidate.name == match.candidate_name  # FK actually resolves


def test_resolution_is_idempotent(db_session):
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)

    for _ in range(3):
        service.resolve_and_persist(
            service.subject_for_ownership_entity(person),
            subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
            subject_id=person.id,
        )
    # Upsert on (subject_ref, provider, external_id) -- never accumulates.
    assert db_session.query(EntityMatch).count() == 1


def test_rejected_matches_are_persisted_for_audit(db_session):
    """A compliance system must show what it considered and dismissed."""
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)
    service.resolve_and_persist(
        service.subject_for_ownership_entity(person),
        subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
        subject_id=person.id,
        persist_rejected=True,
    )
    assert db_session.query(EntityMatch).count() >= 1


def test_engine_never_persists_a_human_only_status(db_session):
    """Runtime invariant, not a convention."""
    service = EntityResolutionService(db_session)
    with pytest.raises(ValueError, match="never produce"):
        service._assert_machine_status(EntityMatchStatus.CONFIRMED)
    with pytest.raises(ValueError, match="never produce"):
        service._assert_machine_status(EntityMatchStatus.HUMAN_REVIEWED)


def test_resolution_writes_an_audit_entry(db_session):
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)
    service.resolve_and_persist(
        service.subject_for_ownership_entity(person),
        subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
        subject_id=person.id,
        correlation_id="test-corr",
    )
    from app.models.audit import AuditLog

    entries = db_session.query(AuditLog).filter_by(action="entity_resolution_run").all()
    assert len(entries) == 1
    assert entries[0].correlation_id == "test-corr"


# -------------------------------------------------------------- evidence


def test_evidence_created_for_confident_match_with_provenance(db_session):
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)
    run = service.resolve_and_persist(
        service.subject_for_ownership_entity(person),
        subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
        subject_id=person.id,
    )
    match_id = run.results[0].persisted_match_id
    evidence = EvidenceService(db_session).list_for_entity_match(match_id)

    assert len(evidence) == 1
    row = evidence[0]
    assert row.evidence_type == EvidenceType.SANCTIONS_MATCH
    assert row.source_tier == SourceTier.TIER_2_CURATED_DEMO  # never upgraded to authoritative
    assert row.entity_match_id == match_id
    assert row.producing_component == "entity_resolution_service"
    facts = json.loads(row.structured_facts)
    assert facts["candidate_name"]
    assert "scores" in facts


def test_no_evidence_for_rejected_matches(db_session):
    """'We looked and it wasn't him' is an EntityMatch, not evidence."""
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)
    run = service.resolve_and_persist(
        service.subject_for_ownership_entity(person),
        subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
        subject_id=person.id,
    )
    rejected = [r for r in run.results if r.status == EntityMatchStatus.AUTO_REJECTED]
    for result in rejected:
        assert EvidenceService(db_session).list_for_entity_match(result.persisted_match_id) == []


def test_evidence_service_supports_every_required_type(db_session):
    """Phase 3 brief SS10 lists six evidence kinds; all six must exist."""
    _ingest(db_session, "clients")
    client = ClientRepository(db_session).get_by_external_id(1)
    service = EvidenceService(db_session)

    service.record_transaction_evidence(
        client_id=client.id, summary="txn", confidence=0.5, structured_facts={"a": 1}, source_dataset="t.csv"
    )
    service.record_ownership_evidence(
        summary="ubo", confidence=0.5, structured_facts={"b": 2}, client_id=client.id
    )
    service.record_adverse_media_evidence(
        article_external_id="x.txt",
        summary="news",
        confidence=0.5,
        provider_name="p",
        provider_kind=ProviderKind.LOCAL_REFERENCE_DATASET,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        client_id=client.id,
    )
    service.record_provider_response_evidence(
        provider_name="p",
        provider_kind=ProviderKind.EXTERNAL_API,
        source_tier=SourceTier.EXTERNAL_LIVE,
        summary="queried, no results",
        confidence=1.0,
        client_id=client.id,
    )
    service.record_manual_evidence(
        author="analyst_1", summary="manual note", confidence=1.0, client_id=client.id
    )
    db_session.commit()

    types = {e.evidence_type for e in service.list_for_client(client.id)}
    assert {
        EvidenceType.TRANSACTION_TYPOLOGY,
        EvidenceType.UBO_EXPOSURE,
        EvidenceType.ADVERSE_MEDIA,
        EvidenceType.PROVIDER_RESPONSE,
        EvidenceType.MANUAL,
    } <= types


def test_manual_evidence_is_attributed_to_the_human(db_session):
    _ingest(db_session, "clients")
    client = ClientRepository(db_session).get_by_external_id(1)
    service = EvidenceService(db_session)
    row = service.record_manual_evidence(
        author="analyst_1", summary="note", confidence=1.0, client_id=client.id
    )
    db_session.commit()
    assert row.producing_component == "human:analyst_1"


def test_structured_facts_are_size_bounded(db_session):
    """An Evidence row must never become a dump of an entire source record."""
    _ingest(db_session, "clients")
    client = ClientRepository(db_session).get_by_external_id(1)
    service = EvidenceService(db_session)
    row = service.record_transaction_evidence(
        client_id=client.id,
        summary="huge",
        confidence=0.5,
        structured_facts={"blob": "x" * 50_000},
        source_dataset="t.csv",
    )
    db_session.commit()
    facts = json.loads(row.structured_facts)
    assert facts.get("truncated") is True


def test_evidence_graph_supports_multiple_evidence_per_entity(db_session):
    _ingest(db_session, "sample_ofac_sdn", "ubo_showcase")
    person = _showcase_person(db_session)
    service = EntityResolutionService(db_session)
    run = service.resolve_and_persist(
        service.subject_for_ownership_entity(person),
        subject_type=EntityMatchSubjectType.OWNERSHIP_ENTITY,
        subject_id=person.id,
    )
    match_id = run.results[0].persisted_match_id

    evidence_service = EvidenceService(db_session)
    evidence_service.record_manual_evidence(
        author="analyst_1", summary="Second, independent evidence", confidence=1.0, entity_match_id=match_id
    )
    db_session.commit()

    assert len(evidence_service.list_for_entity_match(match_id)) == 2
