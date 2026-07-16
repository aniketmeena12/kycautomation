"""
Tier1OpenSanctionsLookupProvider -- a real SanctionsProvider over the full,
authoritative OpenSanctions consolidated targets file
(opensanctions_targets.csv, 1,319,152 rows / 488 MB).

Same streaming/bounded-top-K design as app/providers/tier1_ofac_provider.py
-- see that module's docstring for the memory-safety argument. This file is
~100x larger, so the per-query cost is correspondingly higher (empirically
measured in docs/phase-2-ingestion.md SS3); callers needing this provider
should treat it as a slower, opt-in lookup, not a default hot path -- see
Customer360Service's include_sanctions_lookup flag.

Every result is stamped source_tier=TIER_1_AUTHORITATIVE. Contains zero
entity-specific logic.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from app.core.config import Settings, get_settings
from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier
from app.providers.schemas import ExternalEntityCandidate, ProviderResult
from app.registry.sources import SourceRegistry

CHUNK_SIZE = 50_000
DEFAULT_MAX_RESULTS = 10
DEFAULT_MATCH_THRESHOLD = 70.0

_USE_COLUMNS = ("id", "schema", "name", "aliases", "birth_date", "countries")


class Tier1OpenSanctionsLookupProvider:
    """Implements the SanctionsProvider protocol (app/providers/contracts.py)."""

    provider_name = "tier1_opensanctions_lookup"
    provider_kind = ProviderKind.LOCAL_REFERENCE_DATASET

    def __init__(
        self,
        settings: Settings | None = None,
        registry: SourceRegistry | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or SourceRegistry(self._settings)
        self._max_results = max_results
        self._match_threshold = match_threshold

    def _path(self) -> Path:
        return self._registry.resolve_path(self._registry.get_source("opensanctions"))

    def is_configured(self) -> bool:
        return self._path().is_file()

    def _iter_chunks(self) -> Iterator[pd.DataFrame]:
        """Streaming iterator over the 488 MB file -- only 6 of its 16
        columns are read (usecols=), and only CHUNK_SIZE rows are ever
        materialized at once."""
        yield from pd.read_csv(
            self._path(),
            usecols=list(_USE_COLUMNS),
            dtype=str,
            chunksize=CHUNK_SIZE,
            on_bad_lines="skip",
        )

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
                error_message="opensanctions_targets.csv not found on disk.",
                queried_at=now,
                query_context=query_context,
            )

        try:
            top_scores: list[tuple[float, str]] = []
            best_row_by_id: dict[str, dict] = {}

            for chunk in self._iter_chunks():
                if entity_type:
                    chunk = chunk[chunk["schema"].str.lower() == entity_type.lower()]
                for _, row in chunk.iterrows():
                    entity_id = str(row.get("id") or "").strip()
                    entity_name = str(row.get("name") or "").strip()
                    if not entity_id or not entity_name:
                        continue
                    score = fuzz.token_sort_ratio(name.lower(), entity_name.lower())
                    if score < self._match_threshold:
                        continue
                    if entity_id not in best_row_by_id or score > best_row_by_id[entity_id]["_score"]:
                        best_row_by_id[entity_id] = {**row.to_dict(), "_score": score}
                    if len(top_scores) < self._max_results:
                        heapq.heappush(top_scores, (score, entity_id))
                    elif score > top_scores[0][0]:
                        heapq.heapreplace(top_scores, (score, entity_id))

            top_ids = {eid for _, eid in top_scores}
            candidates = [
                self._row_to_candidate(best_row_by_id[eid], now) for eid in top_ids if eid in best_row_by_id
            ]
        except Exception as exc:
            return ProviderResult(
                status=ProviderResultStatus.ERROR,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.SANCTIONS,
                error_message=f"Failed to stream opensanctions_targets.csv: {exc}",
                queried_at=now,
                query_context=query_context,
            )

        status = ProviderResultStatus.SUCCESS if candidates else ProviderResultStatus.NO_RESULTS
        return ProviderResult(
            status=status,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.SANCTIONS,
            items=candidates,
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
                error_message="opensanctions_targets.csv not found on disk.",
                queried_at=now,
                query_context=query_context,
            )

        try:
            for chunk in self._iter_chunks():
                match = chunk[chunk["id"].astype(str).str.strip() == external_id.strip()]
                if not match.empty:
                    candidate = self._row_to_candidate(match.iloc[0].to_dict(), now)
                    return ProviderResult(
                        status=ProviderResultStatus.SUCCESS,
                        provider=self.provider_name,
                        provider_kind=self.provider_kind,
                        category=ProviderCategory.SANCTIONS,
                        items=[candidate],
                        queried_at=now,
                        query_context=query_context,
                    )
        except Exception as exc:
            return ProviderResult(
                status=ProviderResultStatus.ERROR,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.SANCTIONS,
                error_message=f"Failed to stream opensanctions_targets.csv: {exc}",
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

    def _row_to_candidate(self, row: dict, retrieved_at: datetime) -> ExternalEntityCandidate:
        aliases_raw = row.get("aliases")
        aliases = (
            [a.strip() for a in aliases_raw.split(";") if a.strip()] if isinstance(aliases_raw, str) else []
        )
        countries_raw = row.get("countries")
        countries = (
            [c.strip() for c in countries_raw.split(";") if c.strip()]
            if isinstance(countries_raw, str)
            else []
        )
        birth_date = row.get("birth_date")

        return ExternalEntityCandidate(
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            external_id=str(row.get("id", "")).strip(),
            name=str(row.get("name", "")).strip(),
            aliases=aliases,
            entity_type=(row.get("schema") or "").strip() or None,
            countries=countries,
            nationalities=[],
            dates_of_birth=[birth_date] if isinstance(birth_date, str) and birth_date.strip() else [],
            identifiers=[],
            raw_source_reference="sanctions/opensanctions_targets.csv",
            retrieved_at=retrieved_at,
        )
