"""Monitoring / risk / alerts API endpoints."""


def _ingest(client, *keys):
    for key in keys:
        assert client.post("/api/v1/ingestion/load", json={"source_key": key}).status_code == 200


def _monitor(client, external_id=3):
    return client.post(
        f"/api/v1/monitor/client/{external_id}",
        json={"include_providers": False, "include_resolution": False},
    )


# ------------------------------------------------------------- registry


def test_risk_factors_registry_is_exposed(client):
    r = client.get("/api/v1/risk/factors")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert body["enabled_count"] >= 1
    assert body["contribution_formula"]
    assert body["scoring_logic_version"]
    assert set(body["bands"]) == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    factor = body["factors"][0]
    for field in (
        "id",
        "name",
        "description",
        "weight",
        "category",
        "severity",
        "requires_entity_resolution",
        "confidence_multiplier",
        "enabled",
    ):
        assert field in factor


def test_factors_route_is_not_shadowed_by_the_client_id_route(client):
    """/risk/factors is a literal path competing with /risk/{client_id:int}."""
    assert client.get("/api/v1/risk/factors").status_code == 200


# ------------------------------------------------------------ monitoring


def test_monitor_client_runs_a_cycle(client):
    _ingest(client, "clients")
    r = _monitor(client)
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is None
    assert body["risk"]["score"] > 0
    assert body["new_events"] > 0
    assert body["external_client_id"] == 3


def test_monitor_unknown_client_404s(client):
    r = client.post("/api/v1/monitor/client/999999", json={})
    assert r.status_code == 404


def test_monitor_all_is_paginated(client):
    _ingest(client, "clients")
    r = client.post(
        "/api/v1/monitor/all",
        json={"limit": 3, "include_providers": False, "include_resolution": False},
    )
    assert r.status_code == 200
    assert r.json()["clients_monitored"] == 3


def test_monitor_all_rejects_oversized_limit(client):
    r = client.post("/api/v1/monitor/all", json={"limit": 5000})
    assert r.status_code == 422


def test_monitor_selected_ids(client):
    _ingest(client, "clients")
    r = client.post(
        "/api/v1/monitor/all",
        json={"external_client_ids": [3, 4], "include_providers": False, "include_resolution": False},
    )
    assert r.status_code == 200
    assert {c["external_client_id"] for c in r.json()["cycles"]} == {3, 4}


def test_monitor_high_risk_only(client):
    _ingest(client, "clients")
    r = client.post(
        "/api/v1/monitor/all",
        json={"high_risk_only": True, "limit": 5, "include_providers": False, "include_resolution": False},
    )
    assert r.status_code == 200


# ------------------------------------------------------------------ risk


def test_current_risk_before_monitoring_says_never_monitored(client):
    """Deliberately not 0/LOW -- that would assert 'we checked and they're
    fine' when we never looked."""
    _ingest(client, "clients")
    r = client.get("/api/v1/risk/3")
    assert r.status_code == 200
    assert r.json()["never_monitored"] is True
    assert r.json()["current"] is None


def test_current_risk_after_monitoring(client):
    _ingest(client, "clients")
    _monitor(client)
    r = client.get("/api/v1/risk/3")
    assert r.status_code == 200
    body = r.json()
    assert body["never_monitored"] is False
    assert body["current"]["current_score"] > 0
    assert body["current"]["risk_band"]
    assert body["current"]["scoring_logic_version"]


def test_risk_history_grows_and_never_overwrites(client):
    _ingest(client, "clients")
    _monitor(client)
    _monitor(client)
    r = client.get("/api/v1/risk/history/3")
    assert r.status_code == 200
    assert r.json()["total"] == 2


def test_risk_history_includes_delta_and_contributions(client):
    _ingest(client, "clients")
    _monitor(client)
    _monitor(client)
    snapshots = client.get("/api/v1/risk/history/3").json()["snapshots"]
    latest = snapshots[0]
    assert latest["delta"] is not None
    assert latest["factor_contributions"]
    assert latest["trigger_reason"]


def test_risk_unknown_client_404s(client):
    assert client.get("/api/v1/risk/999999").status_code == 404
    assert client.get("/api/v1/risk/history/999999").status_code == 404


# ---------------------------------------------------------------- events


def test_events_for_client(client):
    _ingest(client, "clients")
    _monitor(client)
    r = client.get("/api/v1/events/3")
    assert r.status_code == 200
    assert r.json()["total"] > 0
    event = r.json()["events"][0]
    assert event["dedup_key"] and event["source"] and event["factor_id"]


def test_events_unknown_client_404s(client):
    assert client.get("/api/v1/events/999999").status_code == 404


# ---------------------------------------------------------------- alerts


def test_alerts_listed_after_monitoring(client):
    _ingest(client, "clients")
    _monitor(client)
    r = client.get("/api/v1/alerts")
    assert r.status_code == 200
    assert r.json()["total"] > 0
    alert = r.json()["alerts"][0]
    assert alert["severity"] and alert["trigger"] and alert["reason"]


def test_alert_detail_includes_linked_events(client):
    _ingest(client, "clients")
    _monitor(client)
    alert_id = client.get("/api/v1/alerts").json()["alerts"][0]["id"]
    r = client.get(f"/api/v1/alerts/{alert_id}")
    assert r.status_code == 200
    assert r.json()["alert"]["id"] == alert_id
    assert "risk_events" in r.json()


def test_alerts_filterable_by_client_and_severity(client):
    _ingest(client, "clients")
    _monitor(client)
    assert client.get("/api/v1/alerts", params={"client_id": 3}).status_code == 200
    assert client.get("/api/v1/alerts", params={"severity": "HIGH"}).status_code == 200
    assert client.get("/api/v1/alerts", params={"status": "OPEN"}).status_code == 200


def test_alerts_unknown_client_404s(client):
    assert client.get("/api/v1/alerts", params={"client_id": 999999}).status_code == 404


def test_alert_unknown_id_404s(client):
    assert client.get("/api/v1/alerts/999999").status_code == 404


def test_no_alert_mutation_endpoints_exist(client):
    """Acting on an alert is a human-review decision reserved for a later
    phase -- there must be no automated way to resolve one."""
    assert client.post("/api/v1/alerts/1", json={}).status_code in (404, 405)
    assert client.patch("/api/v1/alerts/1", json={}).status_code in (404, 405)
    assert client.delete("/api/v1/alerts/1").status_code in (404, 405)
