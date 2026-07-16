"""
Phase 5 -- the investigation API surface (brief SS11).

The default agent is UNCONFIGURED in the test environment (no API key), which
is deliberate rather than incidental: the most likely real-world first contact
with this endpoint is exactly that state, and it must produce an honest,
readable "could not investigate" -- not a 500, and never a fabricated report.
Tests needing a report inject a deterministic provider via the same dependency
seam production uses to swap vendors.
"""

from __future__ import annotations

import pytest

from app.core.enums import InvestigationStatus
from app.ingestion.commands import ingest_dataset
from app.investigation.agent import InvestigationAgent
from app.main import app as fastapi_app
from app.repositories.client_repository import ClientRepository
from app.services.evidence_service import EvidenceService
from app.services.investigation_service import InvestigationOrchestrator
from app.services.monitoring_service import MonitoringService
from tests.fake_llm import RecordingLLMProvider, valid_report_payload


def _prepare(db):
    for key in ("clients", "client_account_mapping"):
        ingest_dataset(db, key)
    client = ClientRepository(db).get_by_external_id(3)
    EvidenceService(db).record_transaction_evidence(
        client_id=client.id,
        summary="22 of 120 transactions carry an upstream OFAC-country flag.",
        confidence=0.9,
        structured_facts={"flagged_count": 22},
        source_dataset="transactions_with_fatf_ofac.csv",
    )
    db.commit()
    MonitoringService(db).monitor_client(client, include_providers=False, include_resolution=False)
    return client


@pytest.fixture()
def stub_agent(db_session):
    """Swap in a deterministic provider at the API layer.

    Overriding the route's orchestrator factory rather than reaching into the
    service proves the injection seam works from the outside -- the same seam a
    real vendor swap would use.
    """
    from app.api.routes import investigations as route

    client = ClientRepository(db_session).get_by_external_id(3)
    evidence_ids = [e.id for e in EvidenceService(db_session).list_for_client(client.id)] if client else [1]
    provider = RecordingLLMProvider(valid_report_payload(evidence_ids))

    original = route._orchestrator
    route._orchestrator = lambda db: InvestigationOrchestrator(db, agent=InvestigationAgent(provider))
    yield provider
    route._orchestrator = original


# --------------------------------------------------------------------- #
# Agent status
# --------------------------------------------------------------------- #


def test_agent_status_reports_the_configured_model_without_a_key(client):
    response = client.get("/api/v1/investigations/agent/status")
    assert response.status_code == 200

    body = response.json()
    assert body["provider"] == "anthropic"
    assert body["model"]  # configuration-driven, never blank
    assert body["configured"] is False  # no key in the test environment
    assert body["prompt_version"]
    assert "never returns a fabricated" in body["note"]


def test_agent_status_path_is_not_shadowed_by_the_id_route(client):
    """/investigations/agent/status must not be parsed as /investigations/{id}.
    Same declaration-order hazard as /risk/factors vs /risk/{client_id}."""
    assert client.get("/api/v1/investigations/agent/status").status_code == 200


# --------------------------------------------------------------------- #
# Running without a configured provider -- the honest default
# --------------------------------------------------------------------- #


def test_run_without_a_configured_provider_records_a_failed_investigation(client, db_session):
    _prepare(db_session)

    response = client.post("/api/v1/investigations/run/3")
    assert response.status_code == 200, "An unavailable model is a result, not a server error."

    body = response.json()
    assert body["investigation"]["status"] == InvestigationStatus.FAILED.value
    assert body["report"] is None
    assert body["investigation"]["summary"] is None
    assert "API key" in body["investigation"]["error_message"]
    assert body["human_review_required"] is True


def test_run_for_an_unknown_client_is_404(client, db_session):
    _prepare(db_session)
    response = client.post("/api/v1/investigations/run/999999")
    assert response.status_code == 404


# --------------------------------------------------------------------- #
# Running with a provider
# --------------------------------------------------------------------- #


def test_run_returns_report_findings_recommendations_and_evaluation(client, db_session, stub_agent):
    _prepare(db_session)

    response = client.post("/api/v1/investigations/run/3", json={"trigger_reason": "API test"})
    assert response.status_code == 200
    body = response.json()

    assert body["investigation"]["status"] == InvestigationStatus.AWAITING_HUMAN_REVIEW.value
    assert body["report"]["summary"]
    assert body["investigation"]["findings"]
    assert body["recommendations"]
    assert body["grounding"]["passed"] is True

    evaluation = body["evaluation"]
    assert evaluation["llm_model"] == "test-model-v1"
    assert evaluation["prompt_version"]
    assert evaluation["latency_ms"] == 42
    assert evaluation["total_tokens"] == 1234 + 567
    assert evaluation["context_hash"]
    assert evaluation["grounding_passed"] is True
    assert evaluation["hallucinated_citation_count"] == 0
    # Null, because no sampling parameter was sent (ADR-025).
    assert evaluation["temperature"] is None


def test_recommendations_can_never_be_approve_or_reject(client, db_session, stub_agent):
    _prepare(db_session)
    body = client.post("/api/v1/investigations/run/3").json()

    actions = {r["action"] for r in body["recommendations"]}
    assert actions
    assert not (actions & {"APPROVE", "REJECT"})


def test_get_investigation_returns_the_stored_report(client, db_session, stub_agent):
    _prepare(db_session)
    created = client.post("/api/v1/investigations/run/3").json()
    investigation_id = created["investigation"]["id"]

    response = client.get(f"/api/v1/investigations/{investigation_id}")
    assert response.status_code == 200
    assert response.json()["report"] == created["report"]


def test_get_unknown_investigation_is_404(client, db_session):
    _prepare(db_session)
    assert client.get("/api/v1/investigations/424242").status_code == 404


def test_list_client_investigations(client, db_session, stub_agent):
    _prepare(db_session)
    client.post("/api/v1/investigations/run/3")
    client.post("/api/v1/investigations/run/3")

    response = client.get("/api/v1/investigations/client/3")
    assert response.status_code == 200
    body = response.json()
    assert body["external_client_id"] == 3
    assert body["total"] == 2
    assert len(body["investigations"]) == 2


def test_rerun_creates_a_second_investigation(client, db_session, stub_agent):
    _prepare(db_session)
    first = client.post("/api/v1/investigations/run/3").json()["investigation"]["id"]

    response = client.post(f"/api/v1/investigations/{first}/rerun")
    assert response.status_code == 200
    assert response.json()["investigation"]["id"] != first
    assert client.get("/api/v1/investigations/client/3").json()["total"] == 2


def test_rerun_of_an_unknown_investigation_is_404(client, db_session):
    _prepare(db_session)
    assert client.post("/api/v1/investigations/424242/rerun").status_code == 404


# --------------------------------------------------------------------- #
# The API cannot decide anything
# --------------------------------------------------------------------- #


def test_there_is_no_endpoint_to_close_or_decide_an_investigation(client):
    """Acting on an investigation is a human compliance decision reserved for a
    later phase. The same read-only boundary Phase 4 drew around alerts."""
    paths = {
        route.path
        for route in fastapi_app.routes
        if getattr(route, "path", "").startswith("/api/v1/investigations")
    }
    for path in paths:
        for banned in ("close", "approve", "reject", "decide", "accept"):
            assert banned not in path.lower(), f"{path} exposes a compliance decision."

    # And nothing may be mutated in place.
    for route in fastapi_app.routes:
        if getattr(route, "path", "").startswith("/api/v1/investigations"):
            assert not (getattr(route, "methods", set()) & {"PUT", "PATCH", "DELETE"})


def test_every_response_states_that_human_review_is_required(client, db_session, stub_agent):
    _prepare(db_session)
    body = client.post("/api/v1/investigations/run/3").json()
    assert body["human_review_required"] is True
    assert body["investigation"]["status"] != "CLOSED"
