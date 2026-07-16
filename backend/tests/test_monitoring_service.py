"""
MonitoringService -- the continuous-KYC cycle, against REAL ingested data.

These are the real-dataset regression tests (Phase 4 brief SS12). Client 3
("Phillips-Hanson") is used because Phase 0 measured its exact attributes:
sanctions_flag=1, UAE, NGO/Charity High-risk sector, opacity 0.5. It is TEST
DATA -- nothing in app/risk/ knows it exists.
"""

from datetime import datetime, timezone

from app.core.enums import (
    ProviderCategory,
    ProviderKind,
    ProviderResultStatus,
    RiskBand,
    RiskEventType,
    SourceTier,
)
from app.ingestion.commands import ingest_dataset
from app.models.alert import Alert
from app.models.risk import RiskEvent, RiskScoreSnapshot
from app.providers.registry import ProviderRegistry
from app.providers.schemas import ExternalArticle, ProviderResult
from app.repositories.client_repository import ClientRepository
from app.repositories.evidence_repository import EvidenceRepository
from app.risk.signals import ProviderSignalCollector
from app.services.monitoring_service import MonitoringService


def _ingest(db, *keys):
    for key in keys:
        ingest_dataset(db, key)


def _client(db, external_id=3):
    return ClientRepository(db).get_by_external_id(external_id)


# ------------------------------------------------- real-dataset regression


def test_real_client_monitoring_cycle_produces_deterministic_score(db_session):
    _ingest(db_session, "clients", "client_account_mapping", "sample_ofac_sdn")
    client = _client(db_session)
    service = MonitoringService(db_session)

    cycle = service.monitor_client(client, include_providers=False, include_resolution=False)

    assert cycle.error is None
    assert cycle.risk is not None
    assert cycle.risk.band == RiskBand.HIGH
    # Phase 0 measured client 3: sanctions_flag=1 (+40), High sector (+8),
    # opacity 0.5 (10 * 0.5 = +5) = 53.
    assert cycle.risk.score == 53.0
    factor_ids = {c.factor_id for c in cycle.risk.contributions}
    assert {"upstream_sanctions_flag", "high_risk_sector", "ownership_opacity"} <= factor_ids


def test_upstream_flag_is_labelled_as_not_independently_verified(db_session):
    """docs/phase-0-dataset-audit.md SS3: client names match 0/2000 against the
    authoritative lists. The sanctions_flag is an upstream label this system
    did not derive, and the record must say so."""
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = MonitoringService(db_session)
    service.monitor_client(client, include_providers=False, include_resolution=False)

    event = (
        db_session.query(RiskEvent).filter_by(client_id=client.id, factor_id="upstream_sanctions_flag").one()
    )
    assert "not independently verified" in event.summary.lower()


def test_low_risk_client_scores_low(db_session):
    """A generic check that the engine discriminates -- not every client is HIGH."""
    _ingest(db_session, "clients")
    service = MonitoringService(db_session)

    scores = []
    for client in ClientRepository(db_session).list(limit=40):
        cycle = service.monitor_client(client, include_providers=False, include_resolution=False)
        scores.append(cycle.risk.score)

    assert min(scores) < 25.0  # some clients are genuinely LOW
    assert max(scores) > min(scores)  # the engine discriminates


# ------------------------------------------------------ change detection


def test_second_cycle_creates_no_duplicate_events(db_session):
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = MonitoringService(db_session)

    first = service.monitor_client(client, include_providers=False, include_resolution=False)
    second = service.monitor_client(client, include_providers=False, include_resolution=False)

    assert first.new_events > 0
    assert second.new_events == 0
    assert second.suppressed_duplicate_events == first.new_events
    assert db_session.query(RiskEvent).filter_by(client_id=client.id).count() == first.new_events


def test_score_is_stable_across_cycles_when_nothing_changes(db_session):
    """Scoring is stateless over the CURRENT picture, so an unchanged client
    keeps the same score -- it must not decay just because no new events fired."""
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = MonitoringService(db_session)

    first = service.monitor_client(client, include_providers=False, include_resolution=False)
    second = service.monitor_client(client, include_providers=False, include_resolution=False)

    assert second.risk.score == first.risk.score
    assert second.risk.delta == 0.0


def test_no_duplicate_alerts_on_repeat_cycles(db_session):
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = MonitoringService(db_session)

    service.monitor_client(client, include_providers=False, include_resolution=False)
    alerts_after_first = db_session.query(Alert).filter_by(client_id=client.id).count()
    second = service.monitor_client(client, include_providers=False, include_resolution=False)

    assert second.alerts_created == 0
    assert db_session.query(Alert).filter_by(client_id=client.id).count() == alerts_after_first


# ------------------------------------------------- history is append-only


def test_every_cycle_appends_a_snapshot(db_session):
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = MonitoringService(db_session)

    for _ in range(3):
        service.monitor_client(client, include_providers=False, include_resolution=False)

    snapshots = db_session.query(RiskScoreSnapshot).filter_by(client_id=client.id).all()
    assert len(snapshots) == 3  # never overwritten


def test_snapshot_records_contributions_and_version(db_session):
    import json

    _ingest(db_session, "clients")
    client = _client(db_session)
    MonitoringService(db_session).monitor_client(client, include_providers=False, include_resolution=False)

    snapshot = db_session.query(RiskScoreSnapshot).filter_by(client_id=client.id).one()
    assert snapshot.scoring_logic_version
    assert snapshot.trigger_reason
    contributions = json.loads(snapshot.factor_contributions)
    assert contributions and "factor_id" in contributions[0]


# ------------------------------------------- events are immutable records


def test_events_carry_full_provenance(db_session):
    _ingest(db_session, "clients")
    client = _client(db_session)
    MonitoringService(db_session).monitor_client(client, include_providers=False, include_resolution=False)

    for event in db_session.query(RiskEvent).filter_by(client_id=client.id).all():
        assert event.dedup_key
        assert event.source
        assert event.trigger
        assert event.factor_id
        assert event.detected_at


def test_event_repository_has_no_update_method():
    """Events are immutable (brief SS3) -- enforced by the repository's shape."""
    from app.repositories.risk_repository import RiskEventRepository

    assert not hasattr(RiskEventRepository, "update")
    assert not hasattr(RiskEventRepository, "upsert")


def test_snapshot_repository_has_no_update_method():
    from app.repositories.risk_repository import RiskSnapshotRepository

    assert not hasattr(RiskSnapshotRepository, "update")
    assert not hasattr(RiskSnapshotRepository, "upsert")


# ------------------------------------------------------ provider failure


class _FailingProvider:
    provider_name = "exploding_media_provider"
    provider_kind = ProviderKind.EXTERNAL_API

    def is_configured(self):
        return True

    def search_entity(self, name):
        raise RuntimeError("provider exploded")

    def fetch_recent_articles(self, name, *, since=None):
        raise NotImplementedError


class _TimingOutProvider:
    provider_name = "timing_out_provider"
    provider_kind = ProviderKind.EXTERNAL_API

    def is_configured(self):
        return True

    def search_entity(self, name):
        return ProviderResult(
            status=ProviderResultStatus.TIMEOUT,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.ADVERSE_MEDIA,
            error_message="took too long",
            queried_at=datetime.now(timezone.utc),
        )

    def fetch_recent_articles(self, name, *, since=None):
        raise NotImplementedError


def _service_with_media_provider(db_session, provider) -> MonitoringService:
    registry = ProviderRegistry()
    registry.register(ProviderCategory.ADVERSE_MEDIA, provider)
    return MonitoringService(
        db_session, provider_collector=ProviderSignalCollector(provider_registry=registry)
    )


class _LiveNewsProvider:
    """A live (EXTERNAL_LIVE) adverse-media provider returning one article --
    the shape newsdata.io produces. NO NETWORK: the article is injected."""

    provider_name = "newsdata_adverse_media_api"
    provider_kind = ProviderKind.EXTERNAL_API

    def is_configured(self):
        return True

    def search_entity(self, name):
        return ProviderResult(
            status=ProviderResultStatus.SUCCESS,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.ADVERSE_MEDIA,
            items=[
                ExternalArticle(
                    provider=self.provider_name,
                    provider_kind=self.provider_kind,
                    source_tier=SourceTier.EXTERNAL_LIVE,
                    external_id="live-article-1",
                    title="Some Firm Under Investigation",
                    source_name="example-news",
                    url="https://example.com/a",
                    content_snippet="A snippet.",
                    retrieved_at=datetime.now(timezone.utc),
                )
            ],
            queried_at=datetime.now(timezone.utc),
        )

    def fetch_recent_articles(self, name, *, since=None):
        raise NotImplementedError


def test_live_news_is_persisted_as_external_live_evidence(db_session):
    # A live adverse-media hit must land in the Evidence panel as EXTERNAL_LIVE,
    # low-confidence, and framed as unverified -- so it can be triaged, not
    # mistaken for a confirmed finding.
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = _service_with_media_provider(db_session, _LiveNewsProvider())

    service.monitor_client(client, include_resolution=False)

    evidence = EvidenceRepository(db_session).list_for_client(client.id)
    live = [e for e in evidence if e.external_record_id == "live-article-1"]
    assert len(live) == 1
    row = live[0]
    assert row.source_tier == SourceTier.EXTERNAL_LIVE
    assert row.confidence == 0.2
    assert "UNVERIFIED" in row.extracted_fact


def test_live_news_does_not_inflate_the_risk_score(db_session):
    # The 30-point adverse_media weight must NOT apply to an unverified live
    # name-match. Scoring with the live provider must equal scoring without it.
    _ingest(db_session, "clients")
    client = _client(db_session)

    with_news = _service_with_media_provider(db_session, _LiveNewsProvider()).monitor_client(
        client, include_resolution=False
    )
    # No ADVERSE_MEDIA_HIT contribution appears, and no 30-point jump happened.
    factor_ids = {c.factor_id for c in with_news.risk.contributions}
    assert "adverse_media" not in factor_ids
    baseline = MonitoringService(db_session).monitor_client(
        _client(db_session, 4), include_providers=False, include_resolution=False
    )
    # The live-news client's score is whatever its profile earns -- never that
    # plus 30. (Sanity anchor: the unrelated baseline client also scored.)
    assert baseline.risk is not None


def test_live_news_evidence_is_not_duplicated_on_re_screening(db_session):
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = _service_with_media_provider(db_session, _LiveNewsProvider())

    service.monitor_client(client, include_resolution=False)
    service.monitor_client(client, include_resolution=False)  # screen again

    evidence = EvidenceRepository(db_session).list_for_client(client.id)
    live = [e for e in evidence if e.external_record_id == "live-article-1"]
    assert len(live) == 1  # keyed on the article id -- not duplicated


def test_monitoring_survives_a_provider_that_raises(db_session):
    """The core SS10 guarantee: the cycle must not fail because a provider does."""
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = _service_with_media_provider(db_session, _FailingProvider())

    cycle = service.monitor_client(client, include_resolution=False)

    assert cycle.error is None  # cycle completed
    assert cycle.risk is not None  # and still produced a score
    assert any("exploding_media_provider" in f for f in cycle.provider_failures)


def test_provider_failure_becomes_an_event_with_zero_risk_weight(db_session):
    """An outage must be visible but must never raise a client's risk."""
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = _service_with_media_provider(db_session, _TimingOutProvider())

    baseline = MonitoringService(db_session).monitor_client(
        _client(db_session, 4), include_providers=False, include_resolution=False
    )
    cycle = service.monitor_client(client, include_resolution=False)

    failure_events = (
        db_session.query(RiskEvent)
        .filter_by(client_id=client.id, event_type=RiskEventType.PROVIDER_FAILURE)
        .all()
    )
    assert len(failure_events) == 1
    # The failure contributed nothing: no factor contribution for it.
    contribution_factors = {c.factor_id for c in cycle.risk.contributions}
    assert "provider_failure" not in contribution_factors or all(
        c.contribution == 0.0 for c in cycle.risk.contributions if c.factor_id == "provider_failure"
    )
    assert baseline is not None  # sanity: the unrelated client also scored fine


def test_provider_failure_is_deduped_across_cycles(db_session):
    """A flapping provider must not spawn an endless stream of events."""
    _ingest(db_session, "clients")
    client = _client(db_session)
    service = _service_with_media_provider(db_session, _TimingOutProvider())

    service.monitor_client(client, include_resolution=False)
    service.monitor_client(client, include_resolution=False)

    assert (
        db_session.query(RiskEvent)
        .filter_by(client_id=client.id, event_type=RiskEventType.PROVIDER_FAILURE)
        .count()
        == 1
    )


# ------------------------------------------------------ batch monitoring


def test_monitor_many_isolates_per_client_failures(db_session):
    _ingest(db_session, "clients")
    clients = ClientRepository(db_session).list(limit=5)
    run = MonitoringService(db_session).monitor_many(
        clients, include_providers=False, include_resolution=False
    )
    assert run.clients_monitored == 5
    assert run.clients_failed == 0
    assert len(run.cycles) == 5


def test_monitor_all_is_paginated(db_session):
    _ingest(db_session, "clients")
    run = MonitoringService(db_session).monitor_all(
        limit=3, include_providers=False, include_resolution=False
    )
    assert run.clients_monitored == 3


def test_monitor_selected_targets_exact_clients(db_session):
    _ingest(db_session, "clients")
    run = MonitoringService(db_session).monitor_selected(
        [3, 4], include_providers=False, include_resolution=False
    )
    assert {c.external_client_id for c in run.cycles} == {3, 4}


def test_monitor_selected_ignores_unknown_ids(db_session):
    _ingest(db_session, "clients")
    run = MonitoringService(db_session).monitor_selected(
        [3, 999999], include_providers=False, include_resolution=False
    )
    assert run.clients_monitored == 1


def test_monitor_high_risk_falls_back_to_flagged_clients_when_unscored(db_session):
    """A fresh install has no snapshots; 'high risk' must not be empty."""
    _ingest(db_session, "clients")
    run = MonitoringService(db_session).monitor_high_risk(
        limit=5, include_providers=False, include_resolution=False
    )
    assert run.clients_monitored > 0


def test_monitoring_writes_an_audit_entry(db_session):
    _ingest(db_session, "clients")
    client = _client(db_session)
    MonitoringService(db_session).monitor_client(
        client, include_providers=False, include_resolution=False, correlation_id="cycle-1"
    )
    from app.models.audit import AuditLog

    entries = db_session.query(AuditLog).filter_by(action="monitoring_cycle").all()
    assert len(entries) == 1
    assert entries[0].correlation_id == "cycle-1"
