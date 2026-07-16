"""ClientLoader -- ingests kyc_profiles/clients_with_fatf_ofac.csv (2,000
rows, ~140 KB). Small enough to read in one pass; still processed in
bounded chunks for consistency with the rest of the ingestion layer and to
cap peak memory."""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.core.enums import ClientType, SectorRisk
from app.ingestion.loaders.base import DatasetLoader
from app.ingestion.normalizers import (
    build_provenance,
    normalize_bool_flag,
    normalize_country_code,
    normalize_name,
    normalize_percentage,
)
from app.ingestion.results import IngestionError, IngestionResult, IngestionResultStatus
from app.repositories.client_repository import ClientRepository

CHUNK_SIZE = 500


class ClientLoader(DatasetLoader):
    source_key = "clients"

    def load(self, db: Session) -> IngestionResult:
        started_at = self._now()
        source = self.source()
        path = self.path()

        if not path.is_file():
            return self._not_found_result(started_at)

        repo = ClientRepository(db)
        errors: list[IngestionError] = []
        seen_keys: set[int] = set()
        created_count = 0
        updated_count = 0
        rows_read = 0

        for chunk in pd.read_csv(path, chunksize=CHUNK_SIZE, on_bad_lines="skip"):
            for row_offset, row in chunk.iterrows():
                rows_read += 1
                try:
                    external_id = int(row["client_id"])
                except (TypeError, ValueError, KeyError):
                    errors.append(
                        IngestionError(
                            row_number=int(row_offset), field="client_id", message="Missing/invalid client_id"
                        )
                    )
                    continue

                if external_id in seen_keys:
                    errors.append(self._duplicate_key_error(int(row_offset), external_id))
                seen_keys.add(external_id)

                try:
                    fields = {
                        "client_name": normalize_name(row.get("client_name")),
                        "client_type": ClientType(str(row.get("client_type")).strip()),
                        "sector": normalize_name(row.get("sector")),
                        "sector_risk": SectorRisk(str(row.get("sector_risk")).strip()),
                        "country": normalize_country_code(row.get("country")),
                        "pep_flag": normalize_bool_flag(row.get("pep_flag")),
                        "sanctions_flag": normalize_bool_flag(row.get("sanctions_flag")),
                        "fatf_country_flag": normalize_bool_flag(row.get("fatf_country_flag")),
                        "ofac_country_flag": normalize_bool_flag(row.get("ofac_country_flag")),
                        "sectoral_sanctions_flag": normalize_bool_flag(row.get("sectoral_sanctions_flag")),
                        "ownership_opacity_score": normalize_percentage(row.get("ownership_opacity_score"))
                        or 0.0,
                        **build_provenance(
                            source_dataset=source.relative_path,
                            source_tier=source.source_tier,
                            source_type=source.source_type,
                        ),
                    }
                except (ValueError, KeyError) as exc:
                    errors.append(
                        IngestionError(row_number=int(row_offset), message=f"Normalization failed: {exc}")
                    )
                    continue

                _, created = repo.upsert(external_client_id=external_id, **fields)
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
            errors=errors[:50],  # cap error list length, never dump unbounded detail
            notes=f"{created_count} created, {updated_count} updated.",
        )
