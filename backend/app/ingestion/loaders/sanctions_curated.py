"""
Curated (Tier-2) sanctions loaders.

CuratedOfacLoader ingests BOTH sample_ofac_sdn.csv (primary entities) and
sample_ofac_alt.csv (aliases) in one pass -- registered under source_key
"sample_ofac_sdn" -- because that's how the two files are actually related
(app/providers/local_sanctions_provider.py already reads them the same way).
"sample_ofac_alt" has no separate loader; app/ingestion/loaders/registry.py
marks it SKIPPED_AUXILIARY when addressed directly.

Every row from these loaders is stamped source_tier=TIER_2_CURATED_DEMO --
never TIER_1_AUTHORITATIVE -- enforced by the registry's SourceDefinition,
not by this loader re-deciding it.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.ingestion.loaders.base import DatasetLoader
from app.ingestion.normalizers import build_provenance, extract_dob_from_remarks, normalize_name
from app.ingestion.results import IngestionError, IngestionResult, IngestionResultStatus
from app.repositories.sanctions_repository import SanctionsRepository


class CuratedOfacLoader(DatasetLoader):
    source_key = "sample_ofac_sdn"
    ALT_SOURCE_KEY = "sample_ofac_alt"

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
        internal_id_by_external: dict[str, int] = {}

        sdn_df = pd.read_csv(path, dtype=str, on_bad_lines="skip")
        for row_offset, row in sdn_df.iterrows():
            rows_read += 1
            ent_num = str(row.get("ent_num", "")).strip()
            if not ent_num or ent_num == "-0-":
                continue  # trailing malformed sentinel row -- see docs/data-dictionary.md

            remarks = row.get("Remarks")
            remarks = None if pd.isna(remarks) else str(remarks).strip() or None

            fields = {
                "name": normalize_name(row.get("SDN_Name")),
                "entity_type": row.get("SDN_Type") if not pd.isna(row.get("SDN_Type")) else None,
                "program_or_dataset": row.get("Program") if not pd.isna(row.get("Program")) else None,
                "country": None,  # sample_ofac_sdn.csv has no separate country column
                "birth_date": extract_dob_from_remarks(remarks),
                "remarks": remarks,
                **provenance,
            }
            entity, created = repo.upsert_entity(external_entity_id=ent_num, **fields)
            internal_id_by_external[ent_num] = entity.id
            if created:
                created_count += 1
            else:
                updated_count += 1

        alt_source = self._registry.get_source(self.ALT_SOURCE_KEY)
        alt_path = self._registry.resolve_path(alt_source) if alt_source else None
        alias_count = 0
        if alt_path and alt_path.is_file():
            alt_df = pd.read_csv(alt_path, dtype=str, on_bad_lines="skip")
            for _, row in alt_df.iterrows():
                ent_num = str(row.get("ent_num", "")).strip()
                alias_name = row.get("alt_name")
                if not ent_num or ent_num == "-0-" or pd.isna(alias_name):
                    continue
                entity_id = internal_id_by_external.get(ent_num)
                if entity_id is None:
                    errors.append(IngestionError(message=f"Alias references unknown ent_num {ent_num}"))
                    continue
                repo.upsert_alias(
                    sanctions_entity_id=entity_id,
                    alias_name=normalize_name(alias_name),
                    alias_type=row.get("alt_type") if not pd.isna(row.get("alt_type")) else None,
                )
                alias_count += 1

        db.commit()

        status = IngestionResultStatus.SUCCESS if not errors else IngestionResultStatus.PARTIAL
        return IngestionResult(
            source_key=self.source_key,
            status=status,
            started_at=started_at,
            completed_at=self._now(),
            records_read=rows_read,
            records_valid=created_count + updated_count + alias_count,
            records_invalid=len(errors),
            errors=errors,
            notes=f"{created_count} entities created, {updated_count} updated, {alias_count} aliases upserted "
            f"(also consumed {self.ALT_SOURCE_KEY}).",
        )
