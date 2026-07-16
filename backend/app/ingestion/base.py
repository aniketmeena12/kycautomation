"""
Ingestion contracts for Phase 1.

Phase 1 implements SCHEMA/HEADER VALIDATION ONLY -- reading a handful of rows
per source to confirm it parses and has the expected shape. No full
production ingestion runs anywhere in this codebase yet (see
app/ingestion/validators.py's per-validator row limits, and
docs/phase-1-foundation.md's "Ingestion Contracts" section for the full
rationale).

--------------------------------------------------------------------------
IDEMPOTENCY STRATEGY (documented now, implemented in a future phase)
--------------------------------------------------------------------------
When real ingestion ships, re-running it must never create duplicate rows.
The natural key for each entity, decided now so Phase 2's upsert logic has
an unambiguous contract to implement against:

  - Client:              external_client_id (unique)
  - Account:              external_account_number (unique)
  - Transaction:          (transaction_source, external_transaction_id) for
                           the shallow file; for SAML-D, which has no native
                           row ID, a deterministic hash of
                           (sender_account, receiver_account, date, time,
                           amount) is the natural key.
  - SanctionsEntity:      (source_type, external_entity_id) -- already
                           enforced as a DB unique constraint.
  - SanctionsAlias:       (sanctions_entity_id, alias_name, alias_type)
  - AdverseMediaArticle:  external_source_key (unique) -- already enforced.
  - OwnershipEntity:      (graph_key, external_entity_id) -- already
                           enforced as a DB unique constraint.
  - OwnershipRelationship: (owner_id, owned_id, source_dataset) -- an edge is
                           only duplicated if the same file lists it twice.

Ingestion jobs should UPSERT on these keys (update the existing row's
provenance/ingested_at, don't insert a second row), not truncate-and-reload.
--------------------------------------------------------------------------
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from app.ingestion.results import IngestionResult
from app.registry.sources import SourceDefinition, SourceRegistry


class SourceValidator(ABC):
    """One validator per source format (CSV/JSON/TEXT). validate() must never
    read more than a small, bounded sample of a source file -- see each
    subclass for its specific row/byte cap."""

    def __init__(self, registry: SourceRegistry | None = None) -> None:
        self._registry = registry or SourceRegistry()

    @abstractmethod
    def validate(self, source: SourceDefinition) -> IngestionResult: ...

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)
