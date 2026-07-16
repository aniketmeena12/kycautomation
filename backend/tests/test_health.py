"""Health endpoints work and readiness reflects real component state."""


def test_liveness(client):
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "alive"}


def test_readiness(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    names = {c["name"] for c in body["checks"]}
    assert {"database", "dataset_registry"} <= names
    for check in body["checks"]:
        assert check["status"] == "ok"
