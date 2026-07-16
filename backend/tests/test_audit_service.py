"""Audit service writes structured audit events safely."""

from app.core.enums import ActorType
from app.models.audit import AuditLog
from app.services.audit_service import MAX_VALUE_LENGTH, record_audit_event


def test_record_audit_event_persists_structured_row(db_session):
    entry = record_audit_event(
        db_session,
        actor_type=ActorType.SYSTEM,
        action="source_validated",
        target_type="DatasetSourceStatus",
        target_id="clients",
        old_value=None,
        new_value={"status": "VALIDATED"},
        correlation_id="corr-1",
    )
    assert entry.id is not None
    assert entry.actor_type == ActorType.SYSTEM
    assert entry.new_value == '{"status": "VALIDATED"}'

    fetched = db_session.get(AuditLog, entry.id)
    assert fetched is not None
    assert fetched.correlation_id == "corr-1"


def test_record_audit_event_handles_none_values(db_session):
    entry = record_audit_event(
        db_session,
        actor_type=ActorType.HUMAN,
        actor_id="reviewer_1",
        action="review_submitted",
    )
    assert entry.old_value is None
    assert entry.new_value is None


def test_record_audit_event_truncates_oversized_values(db_session):
    entry = record_audit_event(
        db_session,
        actor_type=ActorType.AGENT,
        actor_id="adverse_media_agent",
        action="extraction_result",
        old_value="x" * 5000,
    )
    assert len(entry.old_value) <= MAX_VALUE_LENGTH + 60
    assert "truncated" in entry.old_value


def test_record_audit_event_never_raises_on_unserializable_value(db_session):
    class Unserializable:
        def __repr__(self):
            return "<Unserializable>"

    entry = record_audit_event(
        db_session,
        actor_type=ActorType.SYSTEM,
        action="test_action",
        new_value=Unserializable(),
    )
    assert entry.new_value is not None
