"""AccountLoader -- ingests kyc_profiles/client_account_mapping.csv (120
rows). Depends on ClientLoader having already run: a mapping row whose
client_id isn't in the Client table yet is recorded as an error, not
silently dropped or used to fabricate a Client row."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.ingestion.loaders.base import DatasetLoader
from app.ingestion.normalizers import build_provenance
from app.ingestion.results import IngestionError, IngestionResult, IngestionResultStatus
from app.repositories.account_repository import AccountRepository
from app.repositories.client_repository import ClientRepository

CHUNK_SIZE = 500


class AccountLoader(DatasetLoader):
    source_key = "client_account_mapping"

    def load(self, db: Session) -> IngestionResult:
        started_at = self._now()
        source = self.source()
        path = self.path()

        if not path.is_file():
            return self._not_found_result(started_at)

        client_repo = ClientRepository(db)
        account_repo = AccountRepository(db)
        errors: list[IngestionError] = []
        seen_keys: set[int] = set()
        created_count = 0
        updated_count = 0
        rows_read = 0

        for chunk in pd.read_csv(path, chunksize=CHUNK_SIZE, on_bad_lines="skip"):
            for row_offset, row in chunk.iterrows():
                rows_read += 1
                try:
                    external_client_id = int(row["client_id"])
                    external_account_number = int(row["account"])
                except (TypeError, ValueError, KeyError):
                    errors.append(
                        IngestionError(
                            row_number=int(row_offset), message="Missing/invalid client_id or account"
                        )
                    )
                    continue

                if external_account_number in seen_keys:
                    errors.append(self._duplicate_key_error(int(row_offset), external_account_number))
                seen_keys.add(external_account_number)

                client = client_repo.get_by_external_id(external_client_id)
                if client is None:
                    errors.append(
                        IngestionError(
                            row_number=int(row_offset),
                            field="client_id",
                            message=f"Referenced client_id {external_client_id} not found -- run ClientLoader first.",
                            raw_value=str(external_client_id),
                        )
                    )
                    continue

                fields = {
                    "client_id": client.id,
                    **build_provenance(
                        source_dataset=source.relative_path,
                        source_tier=source.source_tier,
                        source_type=source.source_type,
                    ),
                }
                _, created = account_repo.upsert(external_account_number=external_account_number, **fields)
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
