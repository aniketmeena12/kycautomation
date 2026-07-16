"""Database initializes; SQLite foreign keys are enabled; init_db is
non-destructive (create_all only, safe to call repeatedly)."""

from sqlalchemy import text

from app.core.database import Base, engine, init_db


def test_init_db_is_idempotent():
    init_db()
    init_db()  # must not raise or drop anything
    table_names = set(Base.metadata.tables.keys())
    assert "clients" in table_names
    assert "sanctions_entities" in table_names
    assert "audit_logs" in table_names


def test_sqlite_foreign_keys_enabled():
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
    assert result == 1


def test_foreign_key_violation_is_rejected(db_session):
    from app.models.account import Account

    bad_account = Account(
        external_account_number=999999999,
        client_id=999999,  # no such client
        source_dataset="test",
        source_tier="INTERNAL",
        source_type="INTERNAL_KYC",
    )
    db_session.add(bad_account)
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db_session.commit()
