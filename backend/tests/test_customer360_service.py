"""
Customer360Service tests.

Uses a small, fast, synthetic ProviderRegistry (fake providers, no real file
I/O) to test the opt-in-flag wiring and provider-availability reporting
quickly and deterministically -- the REAL providers are already proven
against real data in test_large_dataset_providers.py; duplicating that cost
here would just slow the suite down for no additional coverage.
"""

from datetime import datetime, timezone

from app.core.enums import (
    ClientType,
    ProviderCategory,
    ProviderKind,
    ProviderResultStatus,
    SectorRisk,
    SourceTier,
    SourceType,
)
from app.ingestion.loaders.accounts import AccountLoader
from app.ingestion.loaders.clients import ClientLoader
from app.providers.registry import ProviderRegistry
from app.providers.schemas import ExternalEntityCandidate, ProviderResult
from app.repositories.client_repository import ClientRepository
from app.services.customer360_service import ClientNotFoundError, Customer360Service


class _FakeSanctionsProvider:
    provider_name = "fake_sanctions"
    provider_kind = ProviderKind.LOCAL_REFERENCE_DATASET

    def is_configured(self):
        return True

    def search_entity(self, name, *, country=None, entity_type=None):
        now = datetime.now(timezone.utc)
        return ProviderResult(
            status=ProviderResultStatus.SUCCESS,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.SANCTIONS,
            queried_at=now,
            items=[
                ExternalEntityCandidate(
                    provider=self.provider_name,
                    provider_kind=self.provider_kind,
                    source_tier=SourceTier.TIER_2_CURATED_DEMO,
                    external_id="FAKE-1",
                    name=name,
                    retrieved_at=now,
                )
            ],
        )

    def get_entity(self, external_id):
        raise NotImplementedError


class _FakeAdverseMediaProvider:
    provider_name = "fake_media"
    provider_kind = ProviderKind.LOCAL_REFERENCE_DATASET

    def is_configured(self):
        return True

    def search_entity(self, name):
        now = datetime.now(timezone.utc)
        return ProviderResult(
            status=ProviderResultStatus.NO_RESULTS,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.ADVERSE_MEDIA,
            queried_at=now,
        )

    def fetch_recent_articles(self, name, *, since=None):
        raise NotImplementedError


def _build_fake_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(ProviderCategory.SANCTIONS, _FakeSanctionsProvider())
    registry.register(ProviderCategory.ADVERSE_MEDIA, _FakeAdverseMediaProvider())
    return registry


def _make_client(db_session, external_id=50):
    client, _ = ClientRepository(db_session).upsert(
        external_client_id=external_id,
        client_name="Synthetic 360 Client",
        client_type=ClientType.CORPORATE,
        sector="Tech",
        sector_risk=SectorRisk.LOW,
        country="US",
        source_dataset="t.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    db_session.commit()
    return client


def test_customer_360_fast_path_has_no_provider_calls_by_default(db_session):
    client = _make_client(db_session)
    svc = Customer360Service(db_session, provider_registry=_build_fake_registry())
    result = svc.get_customer_360(client.id)
    assert result.provider_availability == []
    assert result.sanctions_candidates == []
    assert result.adverse_media_candidates == []
    assert result.deep_transaction_summaries == []


def test_customer_360_ownership_is_honestly_empty(db_session):
    client = _make_client(db_session)
    svc = Customer360Service(db_session, provider_registry=_build_fake_registry())
    result = svc.get_customer_360(client.id)
    assert "not linked" in result.ownership_note.lower() or "no ownership" in result.ownership_note.lower()


def test_customer_360_sanctions_opt_in_populates_candidates_with_provenance(db_session):
    client = _make_client(db_session)
    svc = Customer360Service(db_session, provider_registry=_build_fake_registry())
    result = svc.get_customer_360(client.id, include_sanctions_lookup=True)

    assert len(result.sanctions_candidates) == 1
    candidate = result.sanctions_candidates[0]
    assert candidate.provider == "fake_sanctions"
    assert candidate.source_tier == SourceTier.TIER_2_CURATED_DEMO
    assert len(result.provider_availability) == 1
    assert result.provider_availability[0].status == ProviderResultStatus.SUCCESS


def test_customer_360_adverse_media_opt_in_reports_no_results(db_session):
    client = _make_client(db_session)
    svc = Customer360Service(db_session, provider_registry=_build_fake_registry())
    result = svc.get_customer_360(client.id, include_adverse_media_lookup=True)
    assert result.adverse_media_candidates == []
    assert result.provider_availability[0].status == ProviderResultStatus.NO_RESULTS


def test_customer_360_unknown_client_raises():
    from app.core.database import SessionLocal

    db = SessionLocal()
    svc = Customer360Service(db, provider_registry=_build_fake_registry())
    try:
        svc.get_customer_360(9_999_999)
        assert False, "should have raised"
    except ClientNotFoundError as exc:
        assert exc.client_id == 9_999_999
    finally:
        db.close()


def test_customer_360_reflects_real_ingested_accounts(db_session):
    """Integration check against real Phase 0 data: client_id=3 has exactly
    2 mapped accounts (docs/phase-0-dataset-audit.md SS10)."""
    ClientLoader().load(db_session)
    AccountLoader().load(db_session)

    client = ClientRepository(db_session).get_by_external_id(3)
    svc = Customer360Service(db_session, provider_registry=_build_fake_registry())
    result = svc.get_customer_360(client.id)
    assert len(result.accounts) == 2
    assert result.client.external_client_id == 3
    assert result.client.client_name == "Phillips-Hanson"
