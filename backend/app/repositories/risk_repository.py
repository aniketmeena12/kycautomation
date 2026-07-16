"""
Persistence for RiskEvent, RiskScoreSnapshot, and Alert.

Three deliberate omissions, each enforcing a Phase 4 rule:

  * RiskEventRepository has NO update method. Events are immutable
    (brief SS3) -- `create_if_new` inserts or reports a duplicate; nothing
    rewrites an event.
  * RiskSnapshotRepository has NO update method. History is append-only
    (brief SS6: "No overwriting") -- every recalculation inserts a row.
  * AlertRepository's `create_if_new` returns (alert, created) and never
    updates an existing alert's reason. Re-proposing the same alert is a
    no-op, which is what "avoid duplicate alerts" means.

Duplicate detection is a pre-check AND a DB unique constraint. The
constraint is the real guarantee; the pre-check just avoids a noisy
IntegrityError on the common path.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import AlertStatus, RiskBand
from app.models.alert import Alert
from app.models.risk import RiskEvent, RiskScoreSnapshot


class RiskEventRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, event_id: int) -> RiskEvent | None:
        return self._db.get(RiskEvent, event_id)

    def existing_dedup_keys(self, client_id: int) -> set[str]:
        """One query returns every key already seen for this client -- the
        change-detection lookup. Cheaper than probing per candidate event."""
        stmt = select(RiskEvent.dedup_key).where(RiskEvent.client_id == client_id)
        return set(self._db.scalars(stmt))

    def list_for_client(self, client_id: int, *, limit: int = 100) -> list[RiskEvent]:
        stmt = (
            select(RiskEvent)
            .options(selectinload(RiskEvent.evidence))
            .where(RiskEvent.client_id == client_id)
            .order_by(RiskEvent.detected_at.desc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).unique())

    def create_if_new(self, *, client_id: int, dedup_key: str, **fields) -> tuple[RiskEvent | None, bool]:
        """Returns (event, created). (None, False) when the finding is already
        on record -- the caller counts that as a suppressed duplicate."""
        existing = self._db.scalars(
            select(RiskEvent).where(RiskEvent.client_id == client_id, RiskEvent.dedup_key == dedup_key)
        ).first()
        if existing is not None:
            return None, False

        event = RiskEvent(client_id=client_id, dedup_key=dedup_key, **fields)
        self._db.add(event)
        self._db.flush()
        return event, True

    def count_for_client(self, client_id: int) -> int:
        return (
            self._db.scalar(
                select(func.count()).select_from(RiskEvent).where(RiskEvent.client_id == client_id)
            )
            or 0
        )


class RiskSnapshotRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def latest_for_client(self, client_id: int) -> RiskScoreSnapshot | None:
        stmt = (
            select(RiskScoreSnapshot)
            .where(RiskScoreSnapshot.client_id == client_id)
            .order_by(RiskScoreSnapshot.computed_at.desc(), RiskScoreSnapshot.id.desc())
            .limit(1)
        )
        return self._db.scalars(stmt).first()

    def history_for_client(self, client_id: int, *, limit: int = 100) -> list[RiskScoreSnapshot]:
        stmt = (
            select(RiskScoreSnapshot)
            .options(selectinload(RiskScoreSnapshot.triggering_events))
            .where(RiskScoreSnapshot.client_id == client_id)
            .order_by(RiskScoreSnapshot.computed_at.desc(), RiskScoreSnapshot.id.desc())
            .limit(limit)
        )
        return list(self._db.scalars(stmt).unique())

    def append(self, **fields) -> RiskScoreSnapshot:
        """Append-only. There is deliberately no update()."""
        snapshot = RiskScoreSnapshot(**fields)
        self._db.add(snapshot)
        self._db.flush()
        return snapshot

    def count_for_client(self, client_id: int) -> int:
        return (
            self._db.scalar(
                select(func.count())
                .select_from(RiskScoreSnapshot)
                .where(RiskScoreSnapshot.client_id == client_id)
            )
            or 0
        )


class AlertRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, alert_id: int) -> Alert | None:
        stmt = (
            select(Alert)
            .options(selectinload(Alert.risk_events), selectinload(Alert.evidence))
            .where(Alert.id == alert_id)
        )
        return self._db.scalars(stmt).unique().first()

    def list(
        self,
        *,
        client_id: int | None = None,
        status: AlertStatus | None = None,
        severity: RiskBand | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Alert]:
        stmt = select(Alert).options(selectinload(Alert.risk_events), selectinload(Alert.evidence))
        if client_id is not None:
            stmt = stmt.where(Alert.client_id == client_id)
        if status is not None:
            stmt = stmt.where(Alert.status == status)
        if severity is not None:
            stmt = stmt.where(Alert.severity == severity)
        stmt = stmt.order_by(Alert.opened_at.desc(), Alert.id.desc()).offset(offset).limit(limit)
        return list(self._db.scalars(stmt).unique())

    def create_if_new(self, *, client_id: int, dedup_key: str, **fields) -> tuple[Alert | None, bool]:
        existing = self._db.scalars(
            select(Alert).where(Alert.client_id == client_id, Alert.dedup_key == dedup_key)
        ).first()
        if existing is not None:
            return None, False

        alert = Alert(client_id=client_id, dedup_key=dedup_key, **fields)
        self._db.add(alert)
        self._db.flush()
        return alert, True
