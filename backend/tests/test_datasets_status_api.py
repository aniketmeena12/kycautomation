"""GET /api/v1/datasets/status."""


def test_datasets_status_before_any_ingestion(client):
    r = client.get("/api/v1/datasets/status")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 16
    assert body["loaded_count"] == 0
    assert all(d["ingestion_status"] == "NOT_INGESTED" for d in body["datasets"])
    assert all(d["file_available"] for d in body["datasets"])  # real data dir


def test_datasets_status_reflects_loaded_state(client):
    client.post("/api/v1/ingestion/load", json={"source_key": "clients"})
    r = client.get("/api/v1/datasets/status")
    body = r.json()
    assert body["loaded_count"] == 1
    clients_row = next(d for d in body["datasets"] if d["source_key"] == "clients")
    assert clients_row["ingestion_status"] == "LOADED"
    assert clients_row["record_count_ingested"] == 2000


def test_datasets_status_distinguishes_tier1_and_tier2(client):
    r = client.get("/api/v1/datasets/status")
    body = r.json()
    by_key = {d["source_key"]: d for d in body["datasets"]}
    assert by_key["ofac_sdn"]["source_tier"] == "TIER_1_AUTHORITATIVE"
    assert by_key["sample_ofac_sdn"]["source_tier"] == "TIER_2_CURATED_DEMO"
