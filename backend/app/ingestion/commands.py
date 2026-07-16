"""
High-level ingestion commands -- the operations the API layer
(app/api/routes/ingestion.py) and any future CLI/cron job call.

Four operations:
  - validate_sources: header/schema smoke check only (wraps Phase 1's
    validate_all_sources, unchanged).
  - ingest_dataset / ingest_all: real upsert ingestion for FULL_LOAD and
    CURATED_FIXTURE sources only. LOOKUP_ONLY sources (saml_d, ofac_sdn,
    ofac_alt, ofac_add, opensanctions) are never bulk-loaded here -- see
    app/ingestion/loaders/registry.py and docs/phase-2-ingestion.md SS3.
    "Skip large datasets unless explicitly requested" (Phase 2 brief) means:
    by default, ingest_all() omits LOOKUP_ONLY sources from its result list
    entirely; passing include_large=True includes them in the result list
    with a SKIPPED_LOOKUP_ONLY status explaining why -- it does NOT bulk-load
    them, because no such code path exists by design.
  - refresh_dataset / refresh_all: aliases of ingest_dataset / ingest_all.
    Every loader upserts on a natural key (app/ingestion/base.py), so
    "ingest" and "refresh" are the same operation -- re-running is always
    safe and idempotent, never a truncate-and-reload.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.enums import IngestionStatus
from app.ingestion.loaders.registry import AUXILIARY_SOURCE_KEYS, INGESTION_ORDER, get_loader_for
from app.ingestion.results import IngestionResult, IngestionResultStatus
from app.ingestion.validate_all import validate_all_sources
from app.registry.sources import SourceRegistry
from app.repositories.dataset_status_repository import DatasetSourceStatusRepository


def validate_sources(db: Session, source_keys: list[str] | None = None) -> list[IngestionResult]:
    return validate_all_sources(db, source_keys=source_keys)


def ingest_dataset(db: Session, source_key: str, registry: SourceRegistry | None = None) -> IngestionResult:
    registry = registry or SourceRegistry()
    status_repo = DatasetSourceStatusRepository(db)

    if source_key in AUXILIARY_SOURCE_KEYS:
        result = IngestionResult(
            source_key=source_key,
            status=IngestionResultStatus.SKIPPED_AUXILIARY,
            started_at=_now(),
            completed_at=_now(),
            notes="Ingested as part of its primary source's loader (see app/ingestion/loaders/registry.py).",
        )
        return result

    source = registry.get_source(source_key)
    if source is None:
        raise ValueError(f"Unknown source_key: {source_key}")

    loader = get_loader_for(source_key, registry)
    if loader is None:
        result = IngestionResult(
            source_key=source_key,
            status=IngestionResultStatus.SKIPPED_LOOKUP_ONLY,
            started_at=_now(),
            completed_at=_now(),
            notes=(
                f"'{source_key}' is served by a lazy provider lookup (ingestion_strategy="
                f"{source.ingestion_strategy.value}), never bulk-loaded into SQLite. "
                "See app/providers/ and docs/phase-2-ingestion.md SS3."
            ),
        )
        status_repo.upsert(source_key, status=IngestionStatus.NOT_INGESTED, notes=result.notes)
        return result

    result = loader.load(db)
    status_repo.upsert(
        source_key,
        status=_ingestion_status_for(result),
        last_ingested_at=result.completed_at,
        record_count_ingested=result.records_valid,
        notes=result.notes or (result.errors[0].message if result.errors else None),
    )
    return result


def ingest_all(
    db: Session, *, include_large: bool = False, registry: SourceRegistry | None = None
) -> list[IngestionResult]:
    registry = registry or SourceRegistry()
    results: list[IngestionResult] = []

    for source_key in INGESTION_ORDER:
        results.append(ingest_dataset(db, source_key, registry))

    if include_large:
        looked_up_keys = {r.source_key for r in results}
        for source in registry.list_sources():
            if source.source_key in looked_up_keys or source.source_key in AUXILIARY_SOURCE_KEYS:
                continue
            if get_loader_for(source.source_key, registry) is None:
                results.append(ingest_dataset(db, source.source_key, registry))

    # Refresh SQLite's query planner statistics after a bulk load -- see
    # docs/ARCHITECTURE_DECISIONS.md ADR-006. Cheap relative to the ingestion
    # itself and prevents the exact class of bug that ADR documents from
    # silently recurring as new indexes/queries are added.
    db.execute(text("ANALYZE"))
    db.commit()

    return results


def refresh_dataset(db: Session, source_key: str, registry: SourceRegistry | None = None) -> IngestionResult:
    return ingest_dataset(db, source_key, registry)


def refresh_all(
    db: Session, *, include_large: bool = False, registry: SourceRegistry | None = None
) -> list[IngestionResult]:
    return ingest_all(db, include_large=include_large, registry=registry)


def _ingestion_status_for(result: IngestionResult) -> IngestionStatus:
    if result.status == IngestionResultStatus.SUCCESS:
        return IngestionStatus.LOADED
    if result.status == IngestionResultStatus.PARTIAL:
        return IngestionStatus.PARTIALLY_LOADED
    if result.status == IngestionResultStatus.SKIPPED_NOT_FOUND:
        return IngestionStatus.NOT_INGESTED
    return IngestionStatus.VALIDATION_FAILED


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
