"""Phase 6 -- the case-management API (brief SS9)."""

from __future__ import annotations

from app.core.enums import CaseStatus
from app.ingestion.commands import ingest_dataset
from app.investigation.agent import InvestigationAgent
from app.main import app as fastapi_app
from app.repositories.client_repository import ClientRepository
from app.services.evidence_service import EvidenceService
from app.services.investigation_service import InvestigationOrchestrator
from app.services.monitoring_service import MonitoringService
from tests.fake_llm import RecordingLLMProvider, valid_report_payload

REVIEWER = "alice.compliance"


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
    ids = [e.id for e in EvidenceService(db).list_for_client(client.id)]
    InvestigationOrchestrator(
        db, agent=InvestigationAgent(RecordingLLMProvider(valid_report_payload(ids)))
    ).run_for_client(3)
    return client


def _open(client) -> int:
    response = client.post("/api/v1/cases", json={"external_client_id": 3, "reason": "api test"})
    assert response.status_code == 201
    return response.json()["case"]["id"]


# --------------------------------------------------------------------- #


def test_open_and_get_case_aggregates_the_workspace(client, db_session):
    _prepare(db_session)
    case_id = _open(client)

    body = client.get(f"/api/v1/cases/{case_id}").json()
    assert body["case"]["status"] == CaseStatus.OPEN.value
    assert body["case"]["current_risk_score"] == 53.0  # Phase 0-measured client 3
    assert body["customer"]["client"]["client_name"]
    assert body["risk_current"]["band"] == "HIGH"
    assert body["risk_history"]
    assert body["risk_events"]
    assert body["evidence"]
    assert body["investigations"]
    assert body["human_decision_required"] is True
    # The caller never has to guess what it may do.
    assert "CLOSE_CASE" in body["available_actions"]


def test_case_not_found_is_404(client, db_session):
    _prepare(db_session)
    assert client.get("/api/v1/cases/999999").status_code == 404


def test_list_cases_and_filter_by_status(client, db_session):
    _prepare(db_session)
    _open(client)

    body = client.get("/api/v1/cases").json()
    assert body["total"] == 1

    assert client.get("/api/v1/cases?status=OPEN").json()["total"] == 1
    assert client.get("/api/v1/cases?status=CLOSED").json()["total"] == 0


def test_metrics_path_is_not_shadowed_by_the_id_route(client, db_session):
    """/cases/metrics must not parse as /cases/{case_id} -- the same
    declaration-order hazard as /risk/factors."""
    _prepare(db_session)
    _open(client)
    response = client.get("/api/v1/cases/metrics")
    assert response.status_code == 200
    assert response.json()["open_cases"] == 1


def test_timeline_endpoint_returns_ordered_generated_entries(client, db_session):
    _prepare(db_session)
    case_id = _open(client)

    body = client.get(f"/api/v1/cases/{case_id}/timeline").json()["timeline"]
    assert body["total"] > 0
    timestamps = [e["timestamp"] for e in body["entries"]]
    assert timestamps == sorted(timestamps)
    keys = [e["entry_key"] for e in body["entries"]]
    assert len(keys) == len(set(keys))
    assert body["counts_by_type"]


def test_review_endpoint_transitions_the_case(client, db_session):
    _prepare(db_session)
    case_id = _open(client)

    response = client.post(
        f"/api/v1/cases/{case_id}/review",
        json={"reviewer": REVIEWER, "action": "ESCALATE", "comment": "looks bad"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["case"]["status"] == "ESCALATED"
    assert body["reviews"][0]["reviewer_name"] == REVIEWER
    assert body["reviews"][0]["previous_state"] == "OPEN"
    assert body["reviews"][0]["new_state"] == "ESCALATED"
    assert body["reviews"][0]["comment"] == "looks bad"


def test_an_illegal_action_is_409_and_names_what_is_permitted(client, db_session):
    """409, not 400: the request is well-formed and would be valid at another
    time -- it conflicts with the case's CURRENT state."""
    _prepare(db_session)
    case_id = _open(client)
    client.post(f"/api/v1/cases/{case_id}/review", json={"reviewer": REVIEWER, "action": "CLOSE_CASE"})

    response = client.post(
        f"/api/v1/cases/{case_id}/review", json={"reviewer": REVIEWER, "action": "ESCALATE"}
    )
    assert response.status_code == 409
    assert "not permitted" in response.json()["detail"]


def test_a_review_without_a_reviewer_is_rejected(client, db_session):
    """An unattributed compliance decision is not a compliance decision."""
    _prepare(db_session)
    case_id = _open(client)

    assert client.post(f"/api/v1/cases/{case_id}/review", json={"action": "ESCALATE"}).status_code == 422
    assert (
        client.post(
            f"/api/v1/cases/{case_id}/review", json={"reviewer": "", "action": "ESCALATE"}
        ).status_code
        == 422
    )


def test_audit_endpoint_returns_the_immutable_trail(client, db_session):
    _prepare(db_session)
    case_id = _open(client)
    client.post(f"/api/v1/cases/{case_id}/review", json={"reviewer": REVIEWER, "action": "ESCALATE"})

    body = client.get(f"/api/v1/cases/{case_id}/audit").json()
    actions = {e["action"] for e in body["entries"]}
    assert "case_opened" in actions
    assert "case_review:ESCALATE" in actions
    assert "never updated or deleted" in body["note"]

    review_entry = next(e for e in body["entries"] if e["action"] == "case_review:ESCALATE")
    assert review_entry["actor_type"] == "HUMAN"
    assert review_entry["actor_id"] == REVIEWER


def test_sar_generation_and_retrieval(client, db_session):
    _prepare(db_session)
    case_id = _open(client)
    client.post(
        f"/api/v1/cases/{case_id}/review", json={"reviewer": REVIEWER, "action": "CONTINUE_MONITORING"}
    )

    created = client.post(f"/api/v1/cases/{case_id}/sar", json={"requested_by": REVIEWER})
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "DRAFT"
    assert body["requires_human_approval"] is True
    assert "REQUIRES HUMAN APPROVAL" in body["marking"]
    assert len(body["sections"]) == 9
    assert body["cited_evidence_ids"]

    fetched = client.get(f"/api/v1/cases/{case_id}/sar").json()
    assert fetched["sar_ref"] == body["sar_ref"]
    assert client.get(f"/api/v1/cases/{case_id}").json()["case"]["status"] == "SAR_REVIEW"


def test_sar_before_generation_is_404_with_a_useful_hint(client, db_session):
    _prepare(db_session)
    case_id = _open(client)
    response = client.get(f"/api/v1/cases/{case_id}/sar")
    assert response.status_code == 404
    assert f"POST /cases/{case_id}/sar" in response.json()["detail"]


def test_approving_a_sar_requires_a_human_action_not_an_endpoint(client, db_session):
    _prepare(db_session)
    case_id = _open(client)
    client.post(
        f"/api/v1/cases/{case_id}/review", json={"reviewer": REVIEWER, "action": "CONTINUE_MONITORING"}
    )
    sar_id = client.post(f"/api/v1/cases/{case_id}/sar", json={"requested_by": REVIEWER}).json()["id"]

    response = client.post(
        f"/api/v1/cases/{case_id}/review",
        json={"reviewer": "bob.senior", "action": "APPROVE_DRAFT_SAR", "target_id": sar_id, "comment": "ok"},
    )
    assert response.status_code == 200
    assert response.json()["sar_drafts"][0]["status"] == "APPROVED"


def test_the_api_exposes_no_path_that_decides_on_its_own_authority(client):
    """Every consequential action exists only as a reviewer ACTION on /review,
    which requires a named reviewer. No script can complete a compliance
    decision no human made."""
    paths = {
        route.path for route in fastapi_app.routes if getattr(route, "path", "").startswith("/api/v1/cases")
    }
    for path in paths:
        for banned in ("/close", "/approve", "/reject", "/decide", "/confirm", "/file"):
            assert banned not in path.lower(), f"{path} decides without a named reviewer."

    # And nothing is mutable in place -- reviews and audits are append-only.
    for route in fastapi_app.routes:
        if getattr(route, "path", "").startswith("/api/v1/cases"):
            assert not (getattr(route, "methods", set()) & {"PUT", "PATCH", "DELETE"})
