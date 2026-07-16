"""
CuratedOpenSanctionsLoader -- ingests sample_opensanctions.csv (Tier-2
curated fixture, ~21 rows).

Handles a known, real data-quality defect documented in docs/data-
dictionary.md: the row for `os-003401` (Sokolov) is missing a field
delimiter, which shifts `phones`/`emails`/`dataset`/`first_seen` left by one
column for that row only. This loader detects the shift with a GENERIC
heuristic (does the `dataset` field look like a dataset tag or like a date?)
-- not a check for that specific row -- so it would catch the same defect in
any future row, not just the one already found. Detected rows still get
their reliable leading fields (id, schema, name, aliases, birth_date,
countries) ingested; the unreliable shifted fields are nulled rather than
stored as wrong data, and the row is flagged in the result's errors list.
"""

from __future__ import annotations

import re

import pandas as pd
from sqlalchemy.orm import Session

from app.core.enums import SourceType
from app.ingestion.loaders.base import DatasetLoader
from app.ingestion.normalizers import (
    build_provenance,
    normalize_country_code,
    normalize_datetime,
    normalize_name,
)
from app.ingestion.results import IngestionError, IngestionResult, IngestionResultStatus
from app.repositories.sanctions_repository import SanctionsRepository

_DATASET_TAG_PATTERN = re.compile(r"^[a-zA-Z0-9_]+(;[a-zA-Z0-9_]+)*$")
_DATE_LIKE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _looks_column_shifted(dataset_value: str | None) -> bool:
    """Generic heuristic: a valid `dataset` value is a dataset-tag slug
    (e.g. 'us_ofac_sdn' or 'us_ofac_sdn;eu_sanctions_map'). If it instead
    looks like a date, the row's trailing columns are very likely shifted."""
    if not dataset_value:
        return False
    return bool(_DATE_LIKE_PATTERN.match(dataset_value)) and not _DATASET_TAG_PATTERN.match(dataset_value)


class CuratedOpenSanctionsLoader(DatasetLoader):
    source_key = "sample_opensanctions"

    def load(self, db: Session) -> IngestionResult:
        started_at = self._now()
        source = self.source()
        path = self.path()

        if not path.is_file():
            return self._not_found_result(started_at)

        repo = SanctionsRepository(db)
        provenance = build_provenance(
            source_dataset=source.relative_path,
            source_tier=source.source_tier,
            source_type=source.source_type,
        )
        errors: list[IngestionError] = []
        created_count = 0
        updated_count = 0
        rows_read = 0

        df = pd.read_csv(path, dtype=str, on_bad_lines="skip")
        for row_offset, row in df.iterrows():
            rows_read += 1
            external_id = str(row.get("id", "")).strip()
            if not external_id:
                errors.append(IngestionError(row_number=int(row_offset), message="Row missing id"))
                continue

            def val(col: str) -> str | None:
                v = row.get(col)
                return None if v is None or pd.isna(v) else str(v).strip() or None

            dataset_value = val("dataset")
            shifted = _looks_column_shifted(dataset_value)
            if shifted:
                errors.append(
                    IngestionError(
                        row_number=int(row_offset),
                        field="dataset",
                        message=(
                            f"Row {external_id}: 'dataset' value '{dataset_value}' looks column-shifted "
                            "(missing delimiter upstream) -- trailing fields nulled, not trusted."
                        ),
                        raw_value=dataset_value,
                    )
                )

            countries_raw = val("countries")
            first_country = countries_raw.split(";")[0] if countries_raw else None

            remarks_parts = [p for p in (val("addresses"), val("identifiers")) if p]
            remarks = " | ".join(remarks_parts) if remarks_parts and not shifted else None

            fields = {
                "name": normalize_name(row.get("name")),
                "entity_type": val("schema"),
                "program_or_dataset": None if shifted else (val("sanctions") or dataset_value),
                "country": normalize_country_code(first_country),
                "birth_date": (normalize_datetime(val("birth_date")).date() if val("birth_date") else None),
                "remarks": remarks,
                **provenance,
            }
            _, created = repo.upsert_entity(external_entity_id=external_id, **fields)
            if created:
                created_count += 1
            else:
                updated_count += 1

            aliases_raw = val("aliases")
            if aliases_raw and not shifted:
                entity = repo.get_by_external_id(SourceType.CURATED_OPENSANCTIONS, external_id)
                for alias_name in aliases_raw.split(";"):
                    alias_name = normalize_name(alias_name)
                    if alias_name:
                        repo.upsert_alias(sanctions_entity_id=entity.id, alias_name=alias_name)

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
            errors=errors,
            notes=f"{created_count} created, {updated_count} updated. {len(errors)} row(s) flagged.",
        )
