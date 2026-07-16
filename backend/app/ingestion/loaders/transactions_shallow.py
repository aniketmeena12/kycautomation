"""ShallowTransactionLoader -- ingests kyc_profiles/transactions_with_fatf_ofac.csv
(50,000 rows, ~2.9 MB). Chunked reading/committing keeps peak memory and
transaction size bounded, even though the whole file IS fully ingested (this
is the shallow file, not SAML-D -- see docs/phase-0-dataset-audit.md SS3 for
why only this one gets a full-load loader).

Depends on ClientLoader having already run for all 2,000 clients. Resolves
external_client_id -> internal id via one bulk map query, not 50,000
per-row lookups.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.core.enums import TransactionSourceType
from app.ingestion.loaders.base import DatasetLoader
from app.ingestion.normalizers import (
    build_provenance,
    normalize_bool_flag,
    normalize_country_code,
    normalize_datetime,
)
from app.ingestion.results import IngestionError, IngestionResult, IngestionResultStatus
from app.repositories.client_repository import ClientRepository
from app.repositories.transaction_repository import TransactionRepository

CHUNK_SIZE = 2000


class ShallowTransactionLoader(DatasetLoader):
    source_key = "transactions_shallow"

    def load(self, db: Session) -> IngestionResult:
        started_at = self._now()
        source = self.source()
        path = self.path()

        if not path.is_file():
            return self._not_found_result(started_at)

        client_map = ClientRepository(db).map_external_to_internal_ids()
        txn_repo = TransactionRepository(db)
        errors: list[IngestionError] = []
        seen_keys: set[int] = set()
        created_count = 0
        updated_count = 0
        rows_read = 0

        provenance = build_provenance(
            source_dataset=source.relative_path,
            source_tier=source.source_tier,
            source_type=source.source_type,
        )

        for chunk in pd.read_csv(path, chunksize=CHUNK_SIZE, on_bad_lines="skip"):
            for row_offset, row in chunk.iterrows():
                rows_read += 1
                try:
                    external_transaction_id = int(row["transaction_id"])
                    external_client_id = int(row["client_id"])
                except (TypeError, ValueError, KeyError):
                    errors.append(
                        IngestionError(
                            row_number=int(row_offset), message="Missing/invalid transaction_id or client_id"
                        )
                    )
                    continue

                if external_transaction_id in seen_keys:
                    errors.append(self._duplicate_key_error(int(row_offset), external_transaction_id))
                seen_keys.add(external_transaction_id)

                client_internal_id = client_map.get(external_client_id)
                if client_internal_id is None:
                    errors.append(
                        IngestionError(
                            row_number=int(row_offset),
                            field="client_id",
                            message=f"Referenced client_id {external_client_id} not found -- run ClientLoader first.",
                        )
                    )
                    continue

                occurred_at = normalize_datetime(row.get("timestamp"))
                if occurred_at is None:
                    errors.append(
                        IngestionError(
                            row_number=int(row_offset), field="timestamp", message="Unparseable timestamp"
                        )
                    )
                    continue

                fields = {
                    "client_id": client_internal_id,
                    "account_id": None,  # this file has no account reference -- see docs/data-dictionary.md
                    "amount": float(row["amount"]),
                    "currency": None,  # not present in this source file
                    "transaction_type": str(row.get("transaction_type", "")).strip(),
                    "occurred_at": occurred_at,
                    "client_country": normalize_country_code(row.get("client_country")),
                    "counterparty_country": normalize_country_code(row.get("counterparty_country")),
                    "ofac_match_flag": normalize_bool_flag(row.get("ofac_match_flag")),
                    "fatf_country_flag": normalize_bool_flag(row.get("fatf_country_flag")),
                    "structuring_pattern_flag": normalize_bool_flag(row.get("structuring_pattern_flag")),
                    "rapid_movement_flag": normalize_bool_flag(row.get("rapid_movement_flag")),
                    "trade_mispricing_flag": normalize_bool_flag(row.get("trade_mispricing_flag")),
                    **provenance,
                }
                _, created = txn_repo.upsert(
                    transaction_source=TransactionSourceType.SHALLOW_KYC_TXN,
                    external_transaction_id=external_transaction_id,
                    **fields,
                )
                if created:
                    created_count += 1
                else:
                    updated_count += 1

            db.commit()

        status = IngestionResultStatus.SUCCESS if not errors else IngestionResultStatus.PARTIAL
        return IngestionResult(
            source_key=self.source_key,
            status=status,
            started_at=started_at,
            completed_at=self._now(),
            records_read=rows_read,
            records_valid=created_count + updated_count,
            records_invalid=len(errors),
            errors=errors[:50],
            notes=f"{created_count} created, {updated_count} updated.",
        )
