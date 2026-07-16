"""OwnershipLoader -- ingests one UBO graph JSON fixture. One loader instance
per fixture (ubo_simple / ubo_showcase), each producing its own graph_key
(the filename stem) so the two graphs are never traversable as one combined
graph -- see docs/phase-0-dataset-audit.md SS4.7 and
app/repositories/ownership_repository.py.

Two-pass load: entities first (so they get internal IDs), then edges."""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.ingestion.loaders.base import DatasetLoader
from app.ingestion.normalizers import build_provenance, normalize_name, normalize_percentage
from app.ingestion.results import IngestionError, IngestionResult, IngestionResultStatus
from app.repositories.ownership_repository import OwnershipRepository


class OwnershipLoader(DatasetLoader):
    def __init__(self, source_key: str, registry=None) -> None:
        self.source_key = source_key
        super().__init__(registry)

    def load(self, db: Session) -> IngestionResult:
        started_at = self._now()
        source = self.source()
        path = self.path()

        if not path.is_file():
            return self._not_found_result(started_at)

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return IngestionResult(
                source_key=self.source_key,
                status=IngestionResultStatus.FAILED,
                started_at=started_at,
                completed_at=self._now(),
                errors=[IngestionError(message=f"Invalid JSON: {exc}")],
            )

        graph_key = path.stem  # "simple_structure" / "showcase_structure"
        repo = OwnershipRepository(db)
        provenance = build_provenance(
            source_dataset=source.relative_path,
            source_tier=source.source_tier,
            source_type=source.source_type,
        )

        errors: list[IngestionError] = []
        created_count = 0
        updated_count = 0
        entity_id_map: dict[str, int] = {}

        for entity in data.get("entities", []):
            external_id = entity.get("entity_id")
            if not external_id:
                errors.append(IngestionError(message="Entity missing entity_id", raw_value=str(entity)))
                continue
            fields = {
                "name": normalize_name(entity.get("name")),
                "entity_type": entity.get("entity_type"),
                "nationality": entity.get("nationality"),
                "dob": entity.get("dob"),
                "sector": entity.get("sector"),
                "context": entity.get("context"),
                **provenance,
            }
            node, created = repo.upsert_entity(graph_key=graph_key, external_entity_id=external_id, **fields)
            entity_id_map[external_id] = node.id
            if created:
                created_count += 1
            else:
                updated_count += 1

        edge_count = 0
        for edge in data.get("ownership_edges", []):
            owner_external = edge.get("owner_id")
            owned_external = edge.get("owned_id")
            owner_internal = entity_id_map.get(owner_external)
            owned_internal = entity_id_map.get(owned_external)
            if owner_internal is None or owned_internal is None:
                errors.append(
                    IngestionError(
                        message=f"Edge references unknown entity ({owner_external} -> {owned_external})",
                    )
                )
                continue
            repo.upsert_relationship(
                owner_id=owner_internal,
                owned_id=owned_internal,
                percentage=normalize_percentage(edge.get("percentage")) or 0.0,
                description=edge.get("description"),
                **provenance,
            )
            edge_count += 1

        db.commit()

        status = IngestionResultStatus.SUCCESS if not errors else IngestionResultStatus.PARTIAL
        return IngestionResult(
            source_key=self.source_key,
            status=status,
            started_at=started_at,
            completed_at=self._now(),
            records_read=len(data.get("entities", [])) + len(data.get("ownership_edges", [])),
            records_valid=created_count + updated_count + edge_count,
            records_invalid=len(errors),
            errors=errors,
            notes=f"{created_count} entities created, {updated_count} updated, {edge_count} edges upserted (graph_key={graph_key}).",
        )
