"""Ingestion command-layer tests: skip-large-by-default, lookup-only /
auxiliary handling, unknown-key errors, and status persistence."""

import pytest

from app.core.enums import IngestionStatus
from app.ingestion.commands import ingest_dataset, refresh_dataset
from app.ingestion.loaders.registry import AUXILIARY_SOURCE_KEYS
from app.ingestion.results import IngestionResultStatus
from app.repositories.dataset_status_repository import DatasetSourceStatusRepository


def test_ingest_dataset_lookup_only_source_is_skipped_not_bulk_loaded(db_session):
    result = ingest_dataset(db_session, "saml_d")
    assert result.status == IngestionResultStatus.SKIPPED_LOOKUP_ONLY
    from app.models.transaction import Transaction

    assert db_session.query(Transaction).count() == 0  # never bulk-loaded


def test_ingest_dataset_auxiliary_source_is_skipped(db_session):
    assert "sample_ofac_alt" in AUXILIARY_SOURCE_KEYS
    result = ingest_dataset(db_session, "sample_ofac_alt")
    assert result.status == IngestionResultStatus.SKIPPED_AUXILIARY


def test_ingest_dataset_unknown_key_raises(db_session):
    with pytest.raises(ValueError):
        ingest_dataset(db_session, "totally_made_up_source")


def test_ingest_dataset_persists_dataset_source_status(db_session):
    ingest_dataset(db_session, "clients")
    status_repo = DatasetSourceStatusRepository(db_session)
    row = status_repo.get("clients")
    assert row is not None
    assert row.status == IngestionStatus.LOADED
    assert row.record_count_ingested == 2000


def test_refresh_dataset_is_an_alias_and_stays_idempotent(db_session):
    r1 = ingest_dataset(db_session, "clients")
    r2 = refresh_dataset(db_session, "clients")
    assert r1.records_valid == 2000
    assert r2.records_valid == 2000
    assert "2000 updated" in r2.notes
