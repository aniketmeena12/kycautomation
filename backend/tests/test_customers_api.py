"""GET /api/v1/customers, /{client_id}, /{client_id}/360."""


def _ingest_clients_and_accounts(client):
    r1 = client.post("/api/v1/ingestion/load", json={"source_key": "clients"})
    assert r1.status_code == 200
    r2 = client.post("/api/v1/ingestion/load", json={"source_key": "client_account_mapping"})
    assert r2.status_code == 200


def test_list_customers_paginates(client):
    _ingest_clients_and_accounts(client)
    r = client.get("/api/v1/customers?limit=5&offset=0")
    assert r.status_code == 200
    assert len(r.json()) == 5


def test_list_customers_filters_by_mapped_only(client):
    _ingest_clients_and_accounts(client)
    r = client.get("/api/v1/customers?mapped_only=true&limit=500")
    assert r.status_code == 200
    # Verified in Phase 0: exactly 60 of 2000 clients have any mapped account.
    assert len(r.json()) == 60


def test_get_customer_by_external_id(client):
    _ingest_clients_and_accounts(client)
    r = client.get("/api/v1/customers/3")
    assert r.status_code == 200
    body = r.json()
    assert body["external_client_id"] == 3
    assert body["client_name"] == "Phillips-Hanson"


def test_get_customer_before_ingestion_404s(client):
    r = client.get("/api/v1/customers/3")
    assert r.status_code == 404


def test_customer_360_fast_path_returns_real_account_count(client):
    _ingest_clients_and_accounts(client)
    r = client.get("/api/v1/customers/3/360")
    assert r.status_code == 200
    body = r.json()
    assert len(body["accounts"]) == 2
    assert body["ownership_note"]  # honest static note always present
    assert body["provider_availability"] == []  # no opt-in flags requested


def test_customer_360_unknown_client_404s(client):
    r = client.get("/api/v1/customers/999999/360")
    assert r.status_code == 404
