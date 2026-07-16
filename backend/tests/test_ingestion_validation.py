"""Ingestion validators run header/schema checks only, never a full read of
a large source, and validate_all_sources persists real DatasetSourceStatus
rows without ever reaching LOADED (Phase 1 never ingests)."""

import time

from app.core.enums import IngestionStatus
from app.ingestion.results import IngestionResultStatus
from app.ingestion.validate_all import validate_all_sources
from app.ingestion.validators import get_validator_for
from app.models.source_status import DatasetSourceStatus
from app.registry.sources import SourceRegistry


def test_csv_validator_succeeds_on_real_shallow_file():
    registry = SourceRegistry()
    source = registry.get_source("clients")
    validator = get_validator_for(source, registry)
    result = validator.validate(source)
    assert result.status == IngestionResultStatus.SUCCESS
    assert result.records_read == 5  # sampled, not the full 2000 rows


def test_headerless_ofac_schema_recovery_validates():
    registry = SourceRegistry()
    source = registry.get_source("ofac_sdn")
    validator = get_validator_for(source, registry)
    result = validator.validate(source)
    assert result.status == IngestionResultStatus.SUCCESS


def test_saml_d_header_validation_does_not_read_full_951mb_file():
    """Empirical guardrail, not just a design claim: validating SAML-D.csv's
    header must complete almost instantly. If a future change accidentally
    made this read the whole file, this test would start taking many
    seconds and fail the threshold."""
    registry = SourceRegistry()
    source = registry.get_source("saml_d")
    validator = get_validator_for(source, registry)

    started = time.monotonic()
    result = validator.validate(source)
    elapsed = time.monotonic() - started

    assert result.status == IngestionResultStatus.SUCCESS
    assert result.records_read == 5
    assert elapsed < 5.0, f"SAML-D header validation took {elapsed:.2f}s -- likely reading the full file"


def test_opensanctions_header_validation_does_not_read_full_488mb_file():
    registry = SourceRegistry()
    source = registry.get_source("opensanctions")
    validator = get_validator_for(source, registry)

    started = time.monotonic()
    result = validator.validate(source)
    elapsed = time.monotonic() - started

    assert result.status == IngestionResultStatus.SUCCESS
    assert (
        elapsed < 5.0
    ), f"OpenSanctions header validation took {elapsed:.2f}s -- likely reading the full file"


def test_json_validator_on_ubo_fixtures():
    registry = SourceRegistry()
    source = registry.get_source("ubo_showcase")
    validator = get_validator_for(source, registry)
    result = validator.validate(source)
    assert result.status == IngestionResultStatus.SUCCESS
    assert result.records_read == 4


def test_text_validator_on_article_fixtures():
    registry = SourceRegistry()
    source = registry.get_source("article_adversarial")
    validator = get_validator_for(source, registry)
    result = validator.validate(source)
    assert result.status == IngestionResultStatus.SUCCESS


def test_validate_all_sources_persists_status_and_never_reaches_loaded(db_session):
    results = validate_all_sources(db_session, SourceRegistry())
    assert len(results) == 16

    status_rows = db_session.query(DatasetSourceStatus).all()
    assert len(status_rows) == 16
    for row in status_rows:
        assert row.status in (IngestionStatus.VALIDATED, IngestionStatus.VALIDATION_FAILED)
        assert row.status != IngestionStatus.LOADED
        assert row.record_count_ingested is None


def test_validate_all_sources_no_transaction_rows_created(db_session):
    """No full ingestion runs -- the transactions table must remain empty
    after validation."""
    from app.models.transaction import Transaction

    validate_all_sources(db_session, SourceRegistry())
    assert db_session.query(Transaction).count() == 0
