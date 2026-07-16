"""
Tier1OfacLookupProvider -- a real SanctionsProvider over the full,
authoritative OFAC SDN list (ofac_sdn.csv, 19,157 rows / 5.3 MB; ofac_alt.csv,
20,338 rows / 1.1 MB).

NEVER loads either file into memory in full and NEVER writes a row to
SQLite. Every query streams ofac_sdn.csv in bounded chunks (pandas
`chunksize=`), scores each chunk's candidates against the query name with
rapidfuzz, and keeps only a small bounded top-K across the whole stream --
peak memory is O(chunk_size + K), not O(file size), regardless of whether
the file is 5 MB or 500 MB. See docs/phase-2-ingestion.md SS3 for the
measured cost of this approach and its known limitations (no persistent
index, and -- honestly, not yet implemented -- ofac_alt.csv alias matching;
this provider only matches against SDN_Name, not aliases, unlike
app/providers/local_sanctions_provider.py's Tier-2 equivalent, which is
small enough to hold both files in memory. Deferred to a future phase.).

Every result is stamped source_tier=TIER_1_AUTHORITATIVE -- this is real
government sanctions data, never to be confused with the Tier-2 curated demo
fixture (app/providers/local_sanctions_provider.py).

Contains zero entity-specific logic: search_entity(name) runs the identical
streaming comparison for any input string.
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

CHUNK_SIZE = 5000
DEFAULT_MAX_RESULTS = 10
DEFAULT_MATCH_THRESHOLD = 70.0

_SDN_COLUMNS = (
    "ent_num",
    "SDN_Name",
    "SDN_Type",
    "Program",
    "Title",
    "Call_Sign",
    "Vess_type",
    "Tonnage",
    "GRT",
    "Vess_flag",
    "Vess_owner",
    "Remarks",
)


class Tier1OfacLookupProvider:
    """Implements the SanctionsProvider protocol (app/providers/contracts.py)."""

    provider_name = "tier1_ofac_lookup"
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

    def _sdn_path(self) -> Path:
        return self._registry.resolve_path(self._registry.get_source("ofac_sdn"))

    def is_configured(self) -> bool:
        return self._sdn_path().is_file()

    def _iter_sdn_chunks(self) -> Iterator[pd.DataFrame]:
        """Streaming iterator -- one bounded chunk at a time, never the whole file."""
        yield from pd.read_csv(
            self._sdn_path(),
            header=None,
            names=_SDN_COLUMNS,
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
                error_message="ofac_sdn.csv not found on disk.",
                queried_at=now,
                query_context=query_context,
            )

        try:
            # Bounded max-heap of (score, ent_num) -- never grows past max_results.
            top_scores: list[tuple[float, str]] = []
            best_row_by_id: dict[str, dict] = {}

            for chunk in self._iter_sdn_chunks():
                for _, row in chunk.iterrows():
                    ent_num = str(row.get("ent_num") or "").strip()
                    sdn_name = str(row.get("SDN_Name") or "").strip()
                    if not ent_num or ent_num == "-0-" or not sdn_name:
                        continue
                    score = fuzz.token_sort_ratio(name.lower(), sdn_name.lower())
                    if score < self._match_threshold:
                        continue
                    if ent_num not in best_row_by_id or score > best_row_by_id[ent_num]["_score"]:
                        best_row_by_id[ent_num] = {**row.to_dict(), "_score": score}
                    if len(top_scores) < self._max_results:
                        heapq.heappush(top_scores, (score, ent_num))
                    elif score > top_scores[0][0]:
                        heapq.heapreplace(top_scores, (score, ent_num))

            top_ids = {ent_num for _, ent_num in top_scores}
            candidates = [
                self._row_to_candidate(best_row_by_id[eid], now) for eid in top_ids if eid in best_row_by_id
            ]
            candidates.sort(key=lambda c: c.raw_source_reference or "", reverse=False)
        except Exception as exc:  # a malformed/unreadable file must not crash the caller
            return ProviderResult(
                status=ProviderResultStatus.ERROR,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.SANCTIONS,
                error_message=f"Failed to stream ofac_sdn.csv: {exc}",
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
                error_message="ofac_sdn.csv not found on disk.",
                queried_at=now,
                query_context=query_context,
            )

        try:
            for chunk in self._iter_sdn_chunks():
                match = chunk[chunk["ent_num"].astype(str).str.strip() == external_id.strip()]
                if not match.empty:
                    row = match.iloc[0].to_dict()
                    candidate = self._row_to_candidate(row, now)
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
                error_message=f"Failed to stream ofac_sdn.csv: {exc}",
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
        return ExternalEntityCandidate(
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            source_tier=SourceTier.TIER_1_AUTHORITATIVE,
            external_id=str(row.get("ent_num", "")).strip(),
            name=str(row.get("SDN_Name", "")).strip(),
            aliases=[],  # alias resolution deferred -- see docs/phase-2-ingestion.md limitations
            entity_type=(row.get("SDN_Type") or "").strip() or None,
            countries=[],
            nationalities=[],
            dates_of_birth=[],
            identifiers=[],
            raw_source_reference="sanctions/ofac_sdn.csv",
            retrieved_at=retrieved_at,
        )
