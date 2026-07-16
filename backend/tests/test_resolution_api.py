"""Entity-resolution and evidence API endpoints."""


def _ingest(client, *keys):
    for key in keys:
        response = client.post("/api/v1/ingestion/load", json={"source_key": key})
        assert response.status_code == 200


# ------------------------------------------------------------ resolve-pair


def test_resolve_pair_works_on_entities_the_system_has_never_seen(client):
    """The clearest proof the engine is generic: no DB, no ingestion, no
    provider -- two arbitrary entities."""
    r = client.post(
        "/api/v1/entity-resolution/resolve-pair",
        json={
            "subject": {
                "subject_ref": "x:1",
                "name": "Qxzjklm Synthetic Industries GmbH",
                "entity_type": "company",
                "countries": ["DE"],
            },
            "candidate": {
                "subject_ref": "y:1",
                "name": "Qxzjklm Synthetic Industries",
                "entity_type": "company",
                "countries": ["DE"],
            },
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["confidence"] > 85
    assert body["status"] == "HIGH_CONFIDENCE"
    assert body["explanation"]["summary"]
    assert body["scorer_results"]


def test_resolve_pair_reports_conflicts_explicitly(client):
    r = client.post(
        "/api/v1/entity-resolution/resolve-pair",
        json={
            "subject": {"subject_ref": "x:1", "name": "Mohammad Al-Rashid", "entity_type": "individual"},
            "candidate": {"subject_ref": "y:1", "name": "AL-RASHID TRUST", "entity_type": "entity"},
        },
    )
    body = r.json()
    assert body["status"] == "AUTO_REJECTED"
    assert "entity_type" in body["conflicting_attributes"]
    assert body["explanation"]["negative_factors"]


# ---------------------------------------------------------------- resolve


def test_resolve_by_ownership_entity_id(client):
    _ingest(client, "sample_ofac_sdn", "ubo_showcase")
    # Find the individual via the API-independent route: resolve every entity
    # in the graph and assert at least one strong match exists.
    found_high_confidence = False
    for entity_id in range(1, 8):
        r = client.post("/api/v1/entity-resolution/resolve", json={"ownership_entity_id": entity_id})
        if r.status_code != 200:
            continue
        if any(x["status"] == "HIGH_CONFIDENCE" for x in r.json()["results"]):
            found_high_confidence = True
    assert found_high_confidence


def test_resolve_by_client_id(client):
    _ingest(client, "clients", "sample_ofac_sdn")
    r = client.post("/api/v1/entity-resolution/resolve", json={"client_id": 3})
    assert r.status_code == 200
    assert r.json()["subject"]["subject_ref"] == "client:3"


def test_resolve_unknown_client_404s(client):
    r = client.post("/api/v1/entity-resolution/resolve", json={"client_id": 999999})
    assert r.status_code == 404


def test_resolve_requires_exactly_one_subject_source(client):
    both = client.post("/api/v1/entity-resolution/resolve", json={"client_id": 3, "ownership_entity_id": 1})
    assert both.status_code == 422

    neither = client.post("/api/v1/entity-resolution/resolve", json={})
    assert neither.status_code == 422


def test_resolve_with_persist_false_writes_nothing(client, db_session):
    from app.models.resolution import EntityMatch

    _ingest(client, "sample_ofac_sdn", "ubo_showcase")
    before = db_session.query(EntityMatch).count()
    r = client.post("/api/v1/entity-resolution/resolve", json={"ownership_entity_id": 4, "persist": False})
    assert r.status_code == 200
    assert db_session.query(EntityMatch).count() == before


# ------------------------------------------------------------------ batch


def test_batch_resolves_multiple_subjects(client):
    _ingest(client, "clients", "sample_ofac_sdn", "ubo_showcase")
    r = client.post(
        "/api/v1/entity-resolution/batch",
        json={"subjects": [{"client_id": 3}, {"ownership_entity_id": 4}]},
    )
    assert r.status_code == 200
    assert r.json()["total_subjects"] == 2


def test_batch_rejects_oversized_request(client):
    r = client.post(
        "/api/v1/entity-resolution/batch",
        json={"subjects": [{"client_id": 1} for _ in range(51)]},
    )
    assert r.status_code == 422


def test_batch_rejects_empty_request(client):
    r = client.post("/api/v1/entity-resolution/batch", json={"subjects": []})
    assert r.status_code == 422


# ---------------------------------------------------------------- matches


def test_get_match_by_id_and_list_by_subject_ref(client):
    _ingest(client, "sample_ofac_sdn", "ubo_showcase")
    resolve = client.post("/api/v1/entity-resolution/resolve", json={"ownership_entity_id": 4})
    results = resolve.json()["results"]
    assert results
    match_id = results[0]["persisted_match_id"]

    got = client.get(f"/api/v1/entity-resolution/{match_id}")
    assert got.status_code == 200
    assert got.json()["id"] == match_id
    assert got.json()["status"]

    subject_ref = resolve.json()["subject"]["subject_ref"]
    listed = client.get("/api/v1/entity-resolution/matches", params={"subject_ref": subject_ref})
    assert listed.status_code == 200
    assert any(m["id"] == match_id for m in listed.json())


def test_get_unknown_match_404s(client):
    assert client.get("/api/v1/entity-resolution/999999").status_code == 404


def test_list_matches_requires_a_filter(client):
    assert client.get("/api/v1/entity-resolution/matches").status_code == 400


# --------------------------------------------------------------- evidence


def test_evidence_for_entity_match(client):
    _ingest(client, "sample_ofac_sdn", "ubo_showcase")
    resolve = client.post("/api/v1/entity-resolution/resolve", json={"ownership_entity_id": 4})
    high = [r for r in resolve.json()["results"] if r["status"] == "HIGH_CONFIDENCE"]
    assert high
    match_id = high[0]["persisted_match_id"]

    r = client.get(f"/api/v1/evidence/{match_id}")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    row = r.json()["evidence"][0]
    assert row["evidence_type"] == "SANCTIONS_MATCH"
    assert row["source_tier"] == "TIER_2_CURATED_DEMO"  # provenance visible in the API
    assert row["structured_facts"]


def test_evidence_for_client(client):
    _ingest(client, "clients", "sample_ofac_sdn")
    r = client.get("/api/v1/evidence/client/3")
    assert r.status_code == 200
    assert "evidence" in r.json()


def test_evidence_unknown_client_404s(client):
    assert client.get("/api/v1/evidence/client/999999").status_code == 404


def test_evidence_unknown_match_404s(client):
    assert client.get("/api/v1/evidence/999999").status_code == 404
