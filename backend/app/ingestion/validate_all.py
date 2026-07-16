"""
Runs header/schema validation across every registered, enabled source and
records the outcome in DatasetSourceStatus. This is a deliberate, explicit,
on-demand step -- NOT run automatically at application startup, so startup
stays instant regardless of how many sources are registered (see
docs/phase-1-foundation.md's "Application startup must remain fast").

Usage (from backend/):
    python -m app.ingestion.validate_all
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.database import init_db, session_scope
from app.core.enums import IngestionStatus
from app.ingestion.results import IngestionResult, IngestionResultStatus
from app.ingestion.validators import get_validator_for
from app.registry.sources import SourceRegistry
from app.repositories.dataset_status_repository import DatasetSourceStatusRepository


def _status_for(result: IngestionResult, existing: IngestionStatus | None) -> IngestionStatus:
    """Validation must never downgrade a source that Phase 2 ingestion has
    already fully loaded -- re-running `validate` after `ingest` should not
    make /api/v1/datasets/status lie about data that's actually there."""
    if result.status == IngestionResultStatus.SUCCESS:
        if existing in (IngestionStatus.LOADED, IngestionStatus.PARTIALLY_LOADED):
            return existing
        return IngestionStatus.VALIDATED
    if result.status == IngestionResultStatus.SKIPPED_NOT_FOUND:
        return IngestionStatus.NOT_INGESTED
    return IngestionStatus.VALIDATION_FAILED


def validate_all_sources(
    db: Session, registry: SourceRegistry | None = None, source_keys: list[str] | None = None
) -> list[IngestionResult]:
    """Validates every enabled source, or only `source_keys` if given."""
    registry = registry or SourceRegistry()
    status_repo = DatasetSourceStatusRepository(db)
    results: list[IngestionResult] = []

    for source in registry.list_sources():
        if not source.enabled:
            continue
        if source_keys is not None and source.source_key not in source_keys:
            continue

        validator = get_validator_for(source, registry)
        result = validator.validate(source)
        results.append(result)

        existing_row = status_repo.get(source.source_key)
        new_status = _status_for(result, existing_row.status if existing_row else None)
        preserved_count = (
            existing_row.record_count_ingested
            if existing_row and new_status in (IngestionStatus.LOADED, IngestionStatus.PARTIALLY_LOADED)
            else None
        )

        status_repo.upsert(
            source.source_key,
            status=new_status,
            last_validated_at=result.completed_at,
            record_count_ingested=preserved_count,
            notes=result.notes or (result.errors[0].message if result.errors else None),
        )

    return results


def main() -> None:  # pragma: no cover -- exercised via validate_all_sources in tests
    init_db()
    with session_scope() as db:
        results = validate_all_sources(db)
    for r in results:
        print(f"{r.source_key:24s} {r.status.value:20s} {r.notes or ''}")


if __name__ == "__main__":  # pragma: no cover
    main()
