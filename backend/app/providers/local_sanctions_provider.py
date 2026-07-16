"""
LocalCuratedSanctionsProvider -- a real, working SanctionsProvider that
searches the small Tier-2 curated demo fixture files
(sample_ofac_sdn.csv + sample_ofac_alt.csv, ~17-22 rows total).

This is the one concrete provider implementation in Phase 1, included to
prove the provider pattern actually works end-to-end rather than being pure
scaffolding. It is deliberately NOT a stand-in for a real sanctions API:

  - provider_kind is LOCAL_REFERENCE_DATASET, never EXTERNAL_API.
  - source_tier on every result is TIER_2_CURATED_DEMO, never
    TIER_1_AUTHORITATIVE -- callers must not treat a match from this provider
    as equivalent to a real OFAC/OpenSanctions hit.
  - It contains ZERO entity-specific logic. search_entity(name) runs the same
    generic rapidfuzz comparison for any input string -- there is no branch,
    lookup table, or special case for any demo entity's name. See
    tests/test_local_sanctions_provider.py, which proves this with both a
    known fixture name and a nonsense name that matches nothing.

Reading these two files costs a few KB per call -- this is NOT the "full
production dataset ingestion" the project rules forbid in Phase 1 (that
refers to the 19,157-row ofac_sdn.csv / 1,319,152-row opensanctions_targets.csv
Tier-1 files, which this provider does not touch). A real search over the
Tier-1 files is explicitly deferred to the future Entity Resolution Service.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from app.core.config import Settings, get_settings
from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier
from app.providers.schemas import ExternalEntityCandidate, ProviderResult

SDN_SAMPLE_RELATIVE_PATH = "sanctions/sample_ofac_sdn.csv"
ALT_SAMPLE_RELATIVE_PATH = "sanctions/sample_ofac_alt.csv"

DEFAULT_MATCH_THRESHOLD = 70.0  # rapidfuzz token_sort_ratio, 0-100


class LocalCuratedSanctionsProvider:
    """Implements the SanctionsProvider protocol (see app/providers/contracts.py)."""

    provider_name = "local_curated_sanctions_fixture"
    provider_kind = ProviderKind.LOCAL_REFERENCE_DATASET

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _sdn_path(self) -> Path:
        return self._settings.raw_data_dir / SDN_SAMPLE_RELATIVE_PATH

    def _alt_path(self) -> Path:
        return self._settings.raw_data_dir / ALT_SAMPLE_RELATIVE_PATH

    def is_configured(self) -> bool:
        """No API key is needed -- 'configured' means the fixture files are
        present on disk."""
        return self._sdn_path().is_file()

    def _load_candidates(self) -> list[dict]:
        """Reads the two small curated CSVs (never the Tier-1 production
        files) and returns a flat list of {name, external_id, ...} dicts,
        one per primary name or alias."""
        # dtype=str is essential: ent_num values like "001923" are zero-padded
        # identifiers, not numbers. Left to pandas' type inference they'd
        # collapse to 1923 and silently break any future join against the
        # full ofac_sdn.csv, which uses the same zero-padded ent_num scheme.
        sdn_df = pd.read_csv(self._sdn_path(), dtype=str, on_bad_lines="skip")
        rows: list[dict] = []
        for _, row in sdn_df.iterrows():
            ent_num = str(row.get("ent_num", "")).strip()
            if not ent_num or ent_num == "-0-":
                continue
            rows.append(
                {
                    "external_id": ent_num,
                    "name": str(row.get("SDN_Name", "")).strip(),
                    "match_name": str(row.get("SDN_Name", "")).strip(),
                    "entity_type": str(row.get("SDN_Type", "")).strip() or None,
                    "program_or_dataset": str(row.get("Program", "")).strip() or None,
                    "remarks": str(row.get("Remarks", "")).strip() or None,
                }
            )

        alt_path = self._alt_path()
        if alt_path.is_file():
            alt_df = pd.read_csv(alt_path, dtype=str, on_bad_lines="skip")
            alt_by_entity: dict[str, list[str]] = {}
            for _, row in alt_df.iterrows():
                ent_num = str(row.get("ent_num", "")).strip()
                alias_name = str(row.get("alt_name", "")).strip()
                if not ent_num or ent_num == "-0-" or not alias_name:
                    continue
                alt_by_entity.setdefault(ent_num, []).append(alias_name)
                # Also register the alias itself as a separately searchable
                # candidate row, pointing back at the same external_id.
                rows.append(
                    {
                        "external_id": ent_num,
                        "name": alias_name,
                        "match_name": alias_name,
                        "entity_type": None,
                        "program_or_dataset": None,
                        "remarks": None,
                    }
                )
            for row in rows:
                row["aliases"] = alt_by_entity.get(row["external_id"], [])
        else:
            for row in rows:
                row["aliases"] = []

        return rows

    def search_entity(
        self, name: str, *, country: str | None = None, entity_type: str | None = None
    ) -> ProviderResult[ExternalEntityCandidate]:
        now = datetime.now(timezone.utc)
        query_context = {"name": name, "country": country, "entity_type": entity_type}

        if not self.is_configured():
            return ProviderResult(
                status=ProviderResultStatus.NOT_CONFIGURED,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.SANCTIONS,
                error_message="Curated sanctions fixture file not found on disk.",
                queried_at=now,
                query_context=query_context,
            )

        try:
            candidates = self._load_candidates()
        except Exception as exc:  # defensive: a malformed local file must not crash the app
            return ProviderResult(
                status=ProviderResultStatus.ERROR,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.SANCTIONS,
                error_message=f"Failed to read local fixture: {exc}",
                queried_at=now,
                query_context=query_context,
            )

        # Generic fuzzy match -- no entity-specific branching of any kind.
        seen_external_ids: set[str] = set()
        matches: list[ExternalEntityCandidate] = []
        for row in candidates:
            score = fuzz.token_sort_ratio(name.lower(), row["match_name"].lower())
            if score < DEFAULT_MATCH_THRESHOLD:
                continue
            if row["external_id"] in seen_external_ids:
                continue
            seen_external_ids.add(row["external_id"])
            matches.append(
                ExternalEntityCandidate(
                    provider=self.provider_name,
                    provider_kind=self.provider_kind,
                    source_tier=SourceTier.TIER_2_CURATED_DEMO,
                    external_id=row["external_id"],
                    name=row["name"],
                    aliases=row["aliases"],
                    entity_type=row["entity_type"],
                    countries=[],
                    nationalities=[],
                    dates_of_birth=[],
                    identifiers=[],
                    raw_source_reference=SDN_SAMPLE_RELATIVE_PATH,
                    retrieved_at=now,
                )
            )

        status = ProviderResultStatus.SUCCESS if matches else ProviderResultStatus.NO_RESULTS
        return ProviderResult(
            status=status,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.SANCTIONS,
            items=matches,
            queried_at=now,
            query_context=query_context,
        )

    def get_entity(self, external_id: str) -> ProviderResult[ExternalEntityCandidate]:
        now = datetime.now(timezone.utc)
        query_context = {"external_id": external_id}

        if not self.is_configured():
            return ProviderResult(
                status=ProviderResultStatus.NOT_CONFIGURED,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.SANCTIONS,
                error_message="Curated sanctions fixture file not found on disk.",
                queried_at=now,
                query_context=query_context,
            )

        try:
            candidates = self._load_candidates()
        except Exception as exc:
            return ProviderResult(
                status=ProviderResultStatus.ERROR,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.SANCTIONS,
                error_message=f"Failed to read local fixture: {exc}",
                queried_at=now,
                query_context=query_context,
            )

        for row in candidates:
            if row["external_id"] == external_id and row["name"] == row["match_name"]:
                candidate = ExternalEntityCandidate(
                    provider=self.provider_name,
                    provider_kind=self.provider_kind,
                    source_tier=SourceTier.TIER_2_CURATED_DEMO,
                    external_id=row["external_id"],
                    name=row["name"],
                    aliases=row["aliases"],
                    entity_type=row["entity_type"],
                    countries=[],
                    nationalities=[],
                    dates_of_birth=[],
                    identifiers=[],
                    raw_source_reference=SDN_SAMPLE_RELATIVE_PATH,
                    retrieved_at=now,
                )
                return ProviderResult(
                    status=ProviderResultStatus.SUCCESS,
                    provider=self.provider_name,
                    provider_kind=self.provider_kind,
                    category=ProviderCategory.SANCTIONS,
                    items=[candidate],
                    queried_at=now,
                    query_context=query_context,
                )

        return ProviderResult(
            status=ProviderResultStatus.NO_RESULTS,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.SANCTIONS,
            queried_at=now,
            query_context=query_context,
        )
