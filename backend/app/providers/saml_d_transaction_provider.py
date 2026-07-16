"""
SamlDTransactionProvider -- a real TransactionProvider over the full SAML-D
deep AML transaction history (SAML-D.csv, 9,504,852 rows / 951 MB).

Streams the file in bounded chunks (pandas `chunksize=`), filtering each
chunk to rows where the given account appears as sender or receiver, and
returns at most `limit` matches -- the file is NEVER loaded in full and NO
row is ever written to SQLite (see docs/phase-0-dataset-audit.md SS11 /
docs/phase-1-foundation.md's explicit "the 951 MB SAML-D file must NOT be
loaded into SQLite" rule, which this provider is the Phase 2 answer to:
serve it live, never persist it).

Because this is a full linear stream of the largest file in the project,
it is the single most expensive provider call in the system (empirically
measured in docs/phase-2-ingestion.md SS3) -- Customer360Service treats it
as opt-in, never a default. `usecols=` limits the read to only the columns
this provider actually uses, and no fuzzy matching is performed (accounts
are compared by exact string equality), which keeps the per-row cost low
relative to the sanctions providers' rapidfuzz scoring.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.core.config import Settings, get_settings
from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus
from app.providers.schemas import ProviderResult
from app.registry.sources import SourceRegistry

CHUNK_SIZE = 200_000
DEFAULT_LIMIT = 50

_USE_COLUMNS = (
    "Time",
    "Date",
    "Sender_account",
    "Receiver_account",
    "Amount",
    "Payment_currency",
    "Payment_type",
    "Is_laundering",
    "Laundering_type",
)


class SamlDTransactionProvider:
    """Implements the TransactionProvider protocol (app/providers/contracts.py)."""

    provider_name = "saml_d_transaction_lookup"
    provider_kind = ProviderKind.INTERNAL_DATASET

    def __init__(self, settings: Settings | None = None, registry: SourceRegistry | None = None) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or SourceRegistry(self._settings)

    def _path(self) -> Path:
        return self._registry.resolve_path(self._registry.get_source("saml_d"))

    def is_configured(self) -> bool:
        return self._path().is_file()

    def _iter_chunks(self) -> Iterator[pd.DataFrame]:
        yield from pd.read_csv(
            self._path(), usecols=list(_USE_COLUMNS), dtype=str, chunksize=CHUNK_SIZE, on_bad_lines="skip"
        )

    def get_transactions(
        self, account_external_id: str, *, since: datetime | None = None
    ) -> ProviderResult[dict]:
        return self._scan(account_external_id, limit=None, since=since)

    def get_recent_transactions(
        self, account_external_id: str, *, limit: int = DEFAULT_LIMIT
    ) -> ProviderResult[dict]:
        return self._scan(account_external_id, limit=limit, since=None)

    def _scan(
        self, account_external_id: str, *, limit: int | None, since: datetime | None
    ) -> ProviderResult[dict]:
        now = datetime.now(timezone.utc)
        query_context = {"account_external_id": account_external_id, "limit": limit}
        account = str(account_external_id).strip()

        if not self.is_configured():
            return ProviderResult(
                status=ProviderResultStatus.NOT_CONFIGURED,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.TRANSACTION,
                error_message="SAML-D.csv not found on disk.",
                queried_at=now,
                query_context=query_context,
            )

        matches: list[dict] = []
        try:
            for chunk in self._iter_chunks():
                hit = chunk[(chunk["Sender_account"] == account) | (chunk["Receiver_account"] == account)]
                if not hit.empty:
                    matches.extend(hit.to_dict(orient="records"))
                    if limit is not None and len(matches) >= limit:
                        matches = matches[:limit]
                        break
        except Exception as exc:
            return ProviderResult(
                status=ProviderResultStatus.ERROR,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.TRANSACTION,
                error_message=f"Failed to stream SAML-D.csv: {exc}",
                queried_at=now,
                query_context=query_context,
            )

        status = ProviderResultStatus.SUCCESS if matches else ProviderResultStatus.NO_RESULTS
        return ProviderResult(
            status=status,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.TRANSACTION,
            items=matches,
            queried_at=now,
            query_context=query_context,
        )
