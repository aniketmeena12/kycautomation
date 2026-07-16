"""POST /api/v1/ingestion/validate and /load."""


def test_validate_endpoint_returns_all_sources(client):
    r = client.post("/api/v1/ingestion/validate", json={})
    assert r.status_code == 200
    assert len(r.json()["results"]) == 16


def test_validate_endpoint_scoped_to_specific_keys(client):
    r = client.post("/api/v1/ingestion/validate", json={"source_keys": ["clients", "ubo_simple"]})
    assert r.status_code == 200
    keys = {res["source_key"] for res in r.json()["results"]}
    assert keys == {"clients", "ubo_simple"}


def test_load_endpoint_ingests_a_single_source(client):
    r = client.post("/api/v1/ingestion/load", json={"source_key": "clients"})
    assert r.status_code == 200
    result = r.json()["results"][0]
    assert result["status"] == "SUCCESS"
    assert result["records_valid"] == 2000


def test_load_endpoint_rejects_missing_target(client):
    r = client.post("/api/v1/ingestion/load", json={})
    assert r.status_code == 422


def test_load_endpoint_rejects_both_source_key_and_all(client):
    r = client.post("/api/v1/ingestion/load", json={"source_key": "clients", "all": True})
    assert r.status_code == 422


def test_load_endpoint_unknown_source_key_404s(client):
    r = client.post("/api/v1/ingestion/load", json={"source_key": "not_a_real_source"})
    assert r.status_code == 404


def test_load_endpoint_large_source_reports_skipped_not_bulk_loaded(client):
    r = client.post("/api/v1/ingestion/load", json={"source_key": "saml_d"})
    assert r.status_code == 200
    assert r.json()["results"][0]["status"] == "SKIPPED_LOOKUP_ONLY"
