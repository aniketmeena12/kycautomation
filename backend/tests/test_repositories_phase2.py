"""Repository behavior: upsert idempotency, duplicate protection, and
correct aggregation -- using small synthetic fixtures, not the real
50,000-row transaction file (kept fast; the real file is exercised once in
test_ingestion_pipeline.py::test_full_small_dataset_pipeline_against_real_data)."""

from datetime import datetime, timezone

from app.core.enums import (
    ClientType,
    SectorRisk,
    SourceTier,
    SourceType,
    TransactionSourceType,
)
from app.models.client import Client
from app.repositories.account_repository import AccountRepository
from app.repositories.article_repository import ArticleRepository
from app.repositories.client_repository import ClientRepository
from app.repositories.dataset_status_repository import DatasetSourceStatusRepository
from app.repositories.ownership_repository import OwnershipRepository
from app.repositories.sanctions_repository import SanctionsRepository
from app.repositories.transaction_repository import TransactionRepository


def _make_client(db_session, external_id=1) -> Client:
    client, _ = ClientRepository(db_session).upsert(
        external_client_id=external_id,
        client_name="Synthetic Test Client",
        client_type=ClientType.CORPORATE,
        sector="Tech",
        sector_risk=SectorRisk.LOW,
        country="US",
        source_dataset="test.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.commit()
    return client


def test_client_repository_upsert_is_idempotent(db_session):
    repo = ClientRepository(db_session)
    c1, created1 = repo.upsert(
        external_client_id=42,
        client_name="A",
        client_type=ClientType.CORPORATE,
        sector="Tech",
        sector_risk=SectorRisk.LOW,
        country="US",
        source_dataset="t.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.commit()
    c2, created2 = repo.upsert(
        external_client_id=42,
        client_name="A updated",
        client_type=ClientType.CORPORATE,
        sector="Tech",
        sector_risk=SectorRisk.LOW,
        country="US",
        source_dataset="t.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.commit()

    assert created1 is True
    assert created2 is False
    assert c1.id == c2.id
    assert c2.client_name == "A updated"
    assert repo.count() == 1


def test_client_repository_map_external_to_internal_ids(db_session):
    repo = ClientRepository(db_session)
    for i in range(5):
        repo.upsert(
            external_client_id=100 + i,
            client_name=f"C{i}",
            client_type=ClientType.CORPORATE,
            sector="Tech",
            sector_risk=SectorRisk.LOW,
            country="US",
            source_dataset="t.csv",
            source_tier=SourceTier.INTERNAL,
            source_type=SourceType.INTERNAL_KYC,
        )
    db_session.commit()
    mapping = repo.map_external_to_internal_ids()
    assert len(mapping) == 5
    assert all(k in mapping for k in range(100, 105))


def test_account_repository_upsert_idempotent(db_session):
    client = _make_client(db_session)
    repo = AccountRepository(db_session)
    a1, created1 = repo.upsert(
        external_account_number=9999,
        client_id=client.id,
        source_dataset="t.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.commit()
    a2, created2 = repo.upsert(
        external_account_number=9999,
        client_id=client.id,
        source_dataset="t.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.commit()
    assert created1 is True
    assert created2 is False
    assert a1.id == a2.id


def test_transaction_repository_summary_aggregates_flags_correctly(db_session):
    """Regression test for a real bug found in Phase 2: SQLAlchemy's
    Boolean-typed SUM() coerced a real integer count into Python True/False.
    See docs/ARCHITECTURE_DECISIONS.md ADR-007."""
    client = _make_client(db_session, external_id=7)
    repo = TransactionRepository(db_session)
    now = datetime.now(timezone.utc)

    for i in range(5):
        repo.upsert(
            transaction_source=TransactionSourceType.SHALLOW_KYC_TXN,
            external_transaction_id=i,
            client_id=client.id,
            account_id=None,
            amount=10.0 * i,
            currency=None,
            transaction_type="Wire",
            occurred_at=now,
            client_country="US",
            counterparty_country="IR",
            ofac_match_flag=(i < 3),  # 3 of 5 flagged
            fatf_country_flag=False,
            structuring_pattern_flag=False,
            rapid_movement_flag=False,
            trade_mispricing_flag=False,
            source_dataset="t.csv",
            source_tier=SourceTier.INTERNAL,
            source_type=SourceType.INTERNAL_KYC,
        )
    db_session.commit()

    summary = repo.summary_for_client(client.id)
    assert summary["transaction_count"] == 5
    assert summary["flagged_count"] == 3
    assert isinstance(summary["flagged_count"], int)  # not True/False
    assert summary["total_amount"] == 100.0  # 0+10+20+30+40


def test_transaction_repository_upsert_idempotent(db_session):
    client = _make_client(db_session, external_id=8)
    repo = TransactionRepository(db_session)
    now = datetime.now(timezone.utc)
    common = dict(
        client_id=client.id,
        account_id=None,
        currency=None,
        transaction_type="Wire",
        occurred_at=now,
        client_country="US",
        counterparty_country="IR",
        ofac_match_flag=False,
        fatf_country_flag=False,
        structuring_pattern_flag=False,
        rapid_movement_flag=False,
        trade_mispricing_flag=False,
        source_dataset="t.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    t1, created1 = repo.upsert(
        transaction_source=TransactionSourceType.SHALLOW_KYC_TXN,
        external_transaction_id=500,
        amount=1.0,
        **common,
    )
    db_session.commit()
    t2, created2 = repo.upsert(
        transaction_source=TransactionSourceType.SHALLOW_KYC_TXN,
        external_transaction_id=500,
        amount=2.0,
        **common,
    )
    db_session.commit()
    assert created1 is True
    assert created2 is False
    assert t1.id == t2.id
    assert t2.amount == 2.0
    assert repo.count() == 1


def test_transaction_repository_saml_d_rows_with_null_external_id_never_collide(db_session):
    """SAML-D rows have no native external_transaction_id (always None).
    The composite uniqueness must not collapse them into one row -- SQLite
    treats NULL != NULL in a unique index."""
    _make_client(db_session, external_id=9)  # FK target; the binding is unused
    repo = TransactionRepository(db_session)
    now = datetime.now(timezone.utc)
    common = dict(
        client_id=None,
        account_id=None,
        currency="GBP",
        transaction_type="Cash Deposit",
        occurred_at=now,
        source_dataset="SAML-D.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    for i in range(3):
        repo.upsert(
            transaction_source=TransactionSourceType.SAML_D,
            external_transaction_id=None,
            amount=float(i),
            **common,
        )
    db_session.commit()
    assert repo.count() == 3


def test_sanctions_repository_tier_isolation(db_session):
    repo = SanctionsRepository(db_session)
    t1, _ = repo.upsert_entity(
        source_type=SourceType.CURATED_OFAC,
        external_entity_id="X1",
        name="Test Entity",
        source_dataset="t.csv",
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
    )
    t2, _ = repo.upsert_entity(
        source_type=SourceType.OFAC_SDN,
        external_entity_id="X1",
        name="Different Entity, Same Raw ID",
        source_dataset="t.csv",
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
    )
    db_session.commit()
    # Same raw external_entity_id, different source_type -> two distinct rows.
    assert t1.id != t2.id
    tier2_only = repo.list_by_tier(SourceTier.TIER_2_CURATED_DEMO)
    tier1_only = repo.list_by_tier(SourceTier.TIER_1_AUTHORITATIVE)
    assert t1.id in {e.id for e in tier2_only}
    assert t1.id not in {e.id for e in tier1_only}


def test_ownership_repository_graphs_stay_independent(db_session):
    repo = OwnershipRepository(db_session)
    prov = dict(
        source_dataset="ubo.json",
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.UBO_GRAPH_FIXTURE,
    )
    a, _ = repo.upsert_entity(
        graph_key="graph_a", external_entity_id="1", name="A", entity_type="company", **prov
    )
    b, _ = repo.upsert_entity(
        graph_key="graph_b", external_entity_id="1", name="B", entity_type="company", **prov
    )
    db_session.commit()
    # Same external_entity_id, different graph_key -> distinct rows, never conflated.
    assert a.id != b.id
    entities_a, _ = repo.get_graph("graph_a")
    entities_b, _ = repo.get_graph("graph_b")
    assert [e.name for e in entities_a] == ["A"]
    assert [e.name for e in entities_b] == ["B"]


def test_article_repository_upsert_idempotent(db_session):
    repo = ArticleRepository(db_session)
    prov = dict(
        source_dataset="articles/x.txt",
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.ADVERSE_MEDIA_FIXTURE,
    )
    a1, created1 = repo.upsert(external_source_key="x.txt", raw_text="v1", **prov)
    db_session.commit()
    a2, created2 = repo.upsert(external_source_key="x.txt", raw_text="v2", **prov)
    db_session.commit()
    assert created1 is True
    assert created2 is False
    assert a2.raw_text == "v2"


def test_dataset_status_repository_upsert_preserves_notes(db_session):
    from app.core.enums import IngestionStatus

    repo = DatasetSourceStatusRepository(db_session)
    row1 = repo.upsert("clients", status=IngestionStatus.VALIDATED, notes="ok")
    assert row1.status == IngestionStatus.VALIDATED
    row2 = repo.upsert("clients", status=IngestionStatus.LOADED, record_count_ingested=2000, notes="loaded")
    assert row1.id == row2.id
    assert row2.status == IngestionStatus.LOADED
    assert row2.record_count_ingested == 2000
