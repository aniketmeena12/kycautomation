"""Core model relationships work. Test fixtures use synthetic, clearly-fake
identifiers (never real Phase 0 demo entity names) to keep model tests
independent of dataset content."""

from datetime import datetime, timezone

from app.core.enums import ClientType, SectorRisk, SourceTier, SourceType, TransactionSourceType
from app.models.account import Account
from app.models.client import Client
from app.models.transaction import Transaction


def _make_client(db_session, external_id=90001, name="Test Fixture Client A") -> Client:
    client = Client(
        external_client_id=external_id,
        client_name=name,
        client_type=ClientType.CORPORATE,
        sector="Tech",
        sector_risk=SectorRisk.LOW,
        country="US",
        source_dataset="test_fixture.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.add(client)
    db_session.commit()
    db_session.refresh(client)
    return client


def test_client_account_transaction_relationship(db_session):
    client = _make_client(db_session)

    account = Account(
        external_account_number=1234567890,
        client_id=client.id,
        source_dataset="test_fixture.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    txn = Transaction(
        transaction_source=TransactionSourceType.SHALLOW_KYC_TXN,
        client_id=client.id,
        account_id=account.id,
        amount=100.50,
        transaction_type="Wire",
        occurred_at=datetime.now(timezone.utc),
        source_dataset="test_fixture.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.add(txn)
    db_session.commit()

    db_session.refresh(client)
    assert len(client.accounts) == 1
    assert client.accounts[0].external_account_number == 1234567890
    assert client.accounts[0].client is client

    db_session.refresh(account)
    assert len(account.transactions) == 1
    assert account.transactions[0].amount == 100.50


def test_client_external_id_is_unique(db_session):
    _make_client(db_session, external_id=90002, name="Test Fixture Client B")

    duplicate = Client(
        external_client_id=90002,  # same external_client_id
        client_name="Different Name, Same External ID",
        client_type=ClientType.INDIVIDUAL,
        sector="Retail",
        sector_risk=SectorRisk.LOW,
        country="CA",
        source_dataset="test_fixture.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.add(duplicate)

    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_sanctions_entity_and_alias_relationship(db_session):
    from app.models.sanctions import SanctionsAlias, SanctionsEntity

    entity = SanctionsEntity(
        external_entity_id="TEST-999",
        name="Synthetic Test Entity",
        entity_type="individual",
        source_dataset="test_fixture.csv",
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.CURATED_OFAC,
    )
    db_session.add(entity)
    db_session.commit()
    db_session.refresh(entity)

    alias = SanctionsAlias(sanctions_entity_id=entity.id, alias_name="S.T. Entity", alias_type="aka")
    db_session.add(alias)
    db_session.commit()

    db_session.refresh(entity)
    assert len(entity.aliases) == 1
    assert entity.aliases[0].entity is entity
