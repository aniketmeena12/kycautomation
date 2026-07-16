"""
MonitoringService -- the continuous-KYC cycle.

This is what turns the project from a static lookup tool into an
event-driven platform. One cycle, for one client:

    1. Load the client (Customer 360's internal half -- profile, accounts,
       transactions already in the DB).
    2. Collect INTERNAL signals (profile flags, geography, sector, opacity,
       transaction typologies).
    3. Collect RESOLUTION signals (entity resolution against sanctions data).
    4. Collect PROVIDER signals (adverse media), in PARALLEL, tolerating
       failure.
    5. Change detection: only signals whose dedup_key is new become RiskEvents.
    6. Deterministic risk scoring over ALL signals (not just new ones).
    7. Append a RiskScoreSnapshot (history, never overwritten).
    8. Propose + persist alerts.

NO LLM. Every step reads structured data or a provider result.

--------------------------------------------------------------------------
TWO SUBTLETIES WORTH KNOWING
--------------------------------------------------------------------------
1. SCORING USES ALL SIGNALS, EVENTS USE ONLY NEW ONES. A client's risk is a
   function of everything currently true about them, not of what changed this
   cycle. If scoring only saw new signals, a client's score would collapse to
   ~0 on the second run when nothing changed -- obviously wrong. Change
   detection governs EVENT and ALERT creation; scoring is stateless over the
   full current picture.

2. ONE CLIENT'S FAILURE NEVER STOPS A BATCH. `monitor_many` catches per-client
   exceptions into a failed cycle result. Combined with the provider layer's
   own guarantees (ADR-008), a dead provider or one bad row can't take down a
   2,000-client sweep.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.enums import (
    ActorType,
    AlertStatus,
    EntityMatchSubjectType,
    RiskEventStatus,
)
from app.models.client import Client
from app.repositories.client_repository import ClientRepository
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.risk_repository import AlertRepository, RiskEventRepository, RiskSnapshotRepository
from app.risk.alerts import AlertEngine
from app.risk.config import RiskRegistry, get_risk_registry
from app.risk.engine import RiskEngine
from app.risk.schemas import (
    MonitoringCycleResult,
    MonitoringRunResult,
    RiskScoreResult,
    RiskSignal,
)
from app.risk.signals import (
    InternalSignalCollector,
    ProviderSignalCollector,
    ResolutionSignalCollector,
)
from app.services.audit_service import record_audit_event
from app.services.entity_resolution_service import EntityResolutionService

MAX_CONTRIBUTIONS_JSON_CHARS = 8000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MonitoringService:
    def __init__(
        self,
        db: Session,
        registry: RiskRegistry | None = None,
        risk_engine: RiskEngine | None = None,
        alert_engine: AlertEngine | None = None,
        resolution_service: EntityResolutionService | None = None,
        provider_collector: ProviderSignalCollector | None = None,
    ) -> None:
        self._db = db
        self._registry = registry or get_risk_registry()
        self._risk_engine = risk_engine or RiskEngine(self._registry)
        self._alert_engine = alert_engine or AlertEngine(self._registry)
        self._resolution = resolution_service or EntityResolutionService(db)
        self._internal = InternalSignalCollector()
        self._resolution_signals = ResolutionSignalCollector()
        self._providers = provider_collector or ProviderSignalCollector()

        self._clients = ClientRepository(db)
        self._events = RiskEventRepository(db)
        self._snapshots = RiskSnapshotRepository(db)
        self._alerts = AlertRepository(db)
        self._evidence = EvidenceRepository(db)

    # ------------------------------------------------------------------ #
    # Public entry points (brief SS8)
    # ------------------------------------------------------------------ #

    def monitor_client(
        self,
        client: Client,
        *,
        include_providers: bool = True,
        include_resolution: bool = True,
        allow_expensive_providers: bool = False,
        correlation_id: str | None = None,
    ) -> MonitoringCycleResult:
        started = _now()
        try:
            return self._run_cycle(
                client,
                include_providers=include_providers,
                include_resolution=include_resolution,
                allow_expensive_providers=allow_expensive_providers,
                started=started,
                correlation_id=correlation_id,
            )
        except Exception as exc:  # one client must never break a sweep
            self._db.rollback()
            return MonitoringCycleResult(
                client_id=client.id,
                external_client_id=client.external_client_id,
                started_at=started,
                completed_at=_now(),
                error=f"{type(exc).__name__}: {exc}",
            )

    def monitor_many(self, clients: list[Client], **kwargs) -> MonitoringRunResult:
        started = _now()
        cycles = [self.monitor_client(c, **kwargs) for c in clients]
        return MonitoringRunResult(
            cycles=cycles,
            clients_monitored=sum(1 for c in cycles if c.error is None),
            clients_failed=sum(1 for c in cycles if c.error is not None),
            total_new_events=sum(c.new_events for c in cycles),
            total_alerts=sum(c.alerts_created for c in cycles),
            started_at=started,
            completed_at=_now(),
        )

    def monitor_all(self, *, limit: int = 100, offset: int = 0, **kwargs) -> MonitoringRunResult:
        clients = self._clients.list(limit=limit, offset=offset)
        return self.monitor_many(clients, **kwargs)

    def monitor_selected(self, external_client_ids: list[int], **kwargs) -> MonitoringRunResult:
        clients = [
            c for c in (self._clients.get_by_external_id(i) for i in external_client_ids) if c is not None
        ]
        return self.monitor_many(clients, **kwargs)

    def monitor_high_risk(self, *, limit: int = 100, **kwargs) -> MonitoringRunResult:
        """High-risk population = clients whose LATEST snapshot is in a
        configured escalation band. Falls back to profile-flagged clients when
        a client has never been scored -- otherwise a fresh install would have
        an empty 'high risk' set and monitor nothing, which is the opposite of
        useful."""
        escalation = set(self._registry.alerts.escalation_bands)
        candidates: list[Client] = []

        for client in self._clients.list(limit=limit * 5):
            snapshot = self._snapshots.latest_for_client(client.id)
            if snapshot is not None:
                if snapshot.risk_band in escalation:
                    candidates.append(client)
            elif client.sanctions_flag or client.pep_flag:
                candidates.append(client)
            if len(candidates) >= limit:
                break

        return self.monitor_many(candidates, **kwargs)

    # ------------------------------------------------------------------ #
    # The cycle
    # ------------------------------------------------------------------ #

    def _run_cycle(
        self,
        client: Client,
        *,
        include_providers: bool,
        include_resolution: bool,
        allow_expensive_providers: bool,
        started: datetime,
        correlation_id: str | None,
    ) -> MonitoringCycleResult:
        signals: list[RiskSignal] = []
        providers_queried: list[str] = []
        provider_failures: list[str] = []

        # 1-2. Internal signals.
        signals.extend(self._internal.collect(self._db, client))

        # 3. Resolution signals (reuses Phase 3 wholesale -- no new matching logic).
        if include_resolution:
            run = self._resolution.resolve_and_persist(
                self._resolution.subject_for_client(client),
                subject_type=EntityMatchSubjectType.CLIENT,
                subject_id=client.id,
                client_id=client.id,
                allow_expensive_providers=allow_expensive_providers,
                correlation_id=correlation_id,
            )
            signals.extend(self._resolution_signals.collect(client, run.results))
            providers_queried.extend(run.providers_queried)
            provider_failures.extend(run.provider_errors)

        # 4. Provider signals, in parallel across categories.
        if include_providers:
            media_signals, queried, failures = self._collect_provider_signals(client)
            signals.extend(media_signals)
            providers_queried.extend(queried)
            provider_failures.extend(failures)

        # 5. Change detection -> new events only.
        new_events, suppressed = self._create_new_events(client, signals)

        # 6. Score over ALL signals (see module docstring, subtlety 1).
        previous = self._snapshots.latest_for_client(client.id)
        risk = self._risk_engine.score(
            client.id, signals, previous_score=previous.current_score if previous else None
        )

        # 7. Append history.
        self._append_snapshot(client, risk, new_events)

        # 8. Alerts.
        alerts_created = self._create_alerts(client, risk, new_events)

        self._db.commit()

        record_audit_event(
            self._db,
            actor_type=ActorType.SYSTEM,
            actor_id="monitoring_service",
            action="monitoring_cycle",
            target_type="Client",
            target_id=str(client.external_client_id),
            reason=f"Monitoring cycle: {len(signals)} signals, {len(new_events)} new events.",
            old_value=(
                {"score": previous.current_score, "band": previous.risk_band.value} if previous else None
            ),
            new_value={
                "score": risk.score,
                "band": risk.band.value,
                "new_events": len(new_events),
                "alerts": alerts_created,
                "provider_failures": provider_failures,
            },
            correlation_id=correlation_id,
        )

        return MonitoringCycleResult(
            client_id=client.id,
            external_client_id=client.external_client_id,
            signals_collected=len(signals),
            new_events=len(new_events),
            suppressed_duplicate_events=suppressed,
            risk=risk,
            alerts_created=alerts_created,
            providers_queried=sorted(set(providers_queried)),
            provider_failures=provider_failures,
            started_at=started,
            completed_at=_now(),
        )

    def _collect_provider_signals(self, client: Client):
        """Provider categories run concurrently.

        Only ONE category exists today (adverse media), so the pool is
        genuinely just the orchestration seam -- it exists so adding a
        category is a list entry, not a rewrite. Each task is already
        individually timeout/retry-guarded by ProviderExecutionService
        (ADR-008), so this layer only needs to gather results; a task that
        returns failures returns them as data, never as an exception.
        """
        tasks = [lambda: self._providers.collect_adverse_media(client)]
        signals, queried, failures = [], [], []

        with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
            for task_signals, task_queried, task_failures in pool.map(lambda t: t(), tasks):
                signals.extend(task_signals)
                queried.extend(task_queried)
                failures.extend(task_failures)

        return signals, queried, failures

    def _create_new_events(self, client: Client, signals: list[RiskSignal]):
        """Only signals with an unseen dedup_key become events (brief SS9)."""
        seen = self._events.existing_dedup_keys(client.id)
        new_events = []
        suppressed = 0

        for signal in signals:
            factor = next((f for f in self._registry.enabled_factors() if f.matches(signal)), None)
            if factor is None:
                # No configured factor claims this signal -- it isn't a risk
                # event. Surfaced via RiskScoreResult.unmatched_signals.
                continue
            if signal.dedup_key in seen:
                suppressed += 1
                continue

            event, created = self._events.create_if_new(
                client_id=client.id,
                dedup_key=signal.dedup_key,
                event_type=factor.event_type,
                severity=factor.severity,
                confidence=signal.confidence,
                status=RiskEventStatus.OPEN,
                source=signal.source,
                trigger=signal.signal_type,
                summary=signal.summary,
                entity_ref=signal.entity_ref,
                factor_id=factor.id,
                event_timestamp=signal.occurred_at,
            )
            if not created or event is None:
                suppressed += 1
                continue

            for evidence_id in signal.evidence_ids:
                evidence = self._evidence.get_by_id(evidence_id)
                if evidence is not None:
                    event.evidence.append(evidence)

            seen.add(signal.dedup_key)
            new_events.append(event)

        self._db.flush()
        return new_events, suppressed

    def _append_snapshot(self, client: Client, risk: RiskScoreResult, new_events):
        contributions_json = json.dumps([c.model_dump(mode="json") for c in risk.contributions], default=str)
        if len(contributions_json) > MAX_CONTRIBUTIONS_JSON_CHARS:
            contributions_json = json.dumps({"truncated": True, "factor_count": len(risk.contributions)})

        snapshot = self._snapshots.append(
            client_id=client.id,
            previous_score=risk.previous_score,
            current_score=risk.score,
            risk_band=risk.band,
            previous_band=risk.previous_band,
            delta=risk.delta,
            factor_contributions=contributions_json,
            trigger_reason=risk.explanation,
            scoring_logic_version=risk.scoring_logic_version,
            computed_at=risk.computed_at,
        )
        for event in new_events:
            snapshot.triggering_events.append(event)
        self._db.flush()
        return snapshot

    def _create_alerts(self, client: Client, risk: RiskScoreResult, new_events) -> int:
        created = 0
        for proposal in self._alert_engine.propose(risk, new_events):
            alert, was_created = self._alerts.create_if_new(
                client_id=client.id,
                dedup_key=proposal.dedup_key,
                severity=proposal.severity,
                trigger=proposal.trigger,
                reason=proposal.reason,
                risk_delta=proposal.risk_delta,
                status=AlertStatus.OPEN,
                triggering_risk_event_id=proposal.risk_event_ids[0] if proposal.risk_event_ids else None,
            )
            if not was_created or alert is None:
                continue
            for event in new_events:
                if event.id in proposal.risk_event_ids:
                    alert.risk_events.append(event)
            created += 1

        self._db.flush()
        return created
