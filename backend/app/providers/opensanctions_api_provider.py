"""
OpenSanctionsAPIProvider -- a REAL, live SanctionsProvider that screens a name
against the OpenSanctions matching API (api.opensanctions.org/match).

This is the second external slot to graduate from placeholder to integration,
and the more sensitive one: the trial key is 50 requests PER MONTH. So this
provider is registered under the same EXPENSIVE_PROVIDERS gate as the streaming
Tier-1 lookup -- it fires only when a caller passes allow_expensive_providers=
True, never on a routine monitoring cycle. A demo runs it deliberately on one
case; nothing runs it in the background. conftest also blanks SANCTIONS_API_KEY,
so the test suite can never spend a request.

Two boundaries this provider does NOT cross, on purpose:
  * It returns CANDIDATES, never a decision. OpenSanctions ships a `score` and a
    `match` boolean; we carry the score only as provenance text and let the
    deterministic resolution scorers compute confidence from the actual field
    overlap. A vendor's own match flag never becomes our authoritative signal --
    same rule that keeps the LLM out of scoring.
  * Provenance is EXTERNAL_LIVE, never a curated or authoritative tier. A live
    third-party API hit is its own class of evidence and must not be presentable
    as a bulk Tier-1 list entry.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.config import Settings, get_settings
from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier
from app.providers.schemas import ExternalEntityCandidate, ProviderResult

_DEFAULT_BASE_URL = "https://api.opensanctions.org"
_DEFAULT_TIMEOUT_SECONDS = 20.0
# The match API accepts several named queries in ONE request. Sending a Person
# and a LegalEntity query together means a single billed call catches both a
# sanctioned individual and a sanctioned company for a name whose type we do not
# know -- recall without a second request against the 50/mo budget.
_SCHEMA_FOR = {
    "PERSON": "Person",
    "PEOPLE": "Person",
    "INDIVIDUAL": "Person",
    "COMPANY": "Company",
    "ORGANIZATION": "Organization",
    "ORGANISATION": "Organization",
    "LEGAL_ENTITY": "LegalEntity",
    "ENTITY": "LegalEntity",
}


class OpenSanctionsAPIProvider:
    """Implements the SanctionsProvider protocol (app/providers/contracts.py)."""

    provider_name = "opensanctions_match_api"
    provider_kind = ProviderKind.EXTERNAL_API

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(self._settings.sanctions_api_key)

    # -- protocol surface ------------------------------------------------ #

    def search_entity(
        self, name: str, *, country: str | None = None, entity_type: str | None = None
    ) -> ProviderResult[ExternalEntityCandidate]:
        now = datetime.now(timezone.utc)
        query_context = {"name": name, "country": country, "entity_type": entity_type}

        if not self.is_configured():
            return self._result(
                ProviderResultStatus.NOT_CONFIGURED,
                now,
                query_context,
                error="opensanctions_match_api has no API key configured (set SANCTIONS_API_KEY to enable live screening).",
            )

        queries = self._build_queries(name, country=country, entity_type=entity_type)
        base = self._settings.sanctions_api_base_url or _DEFAULT_BASE_URL

        try:
            response = httpx.post(
                f"{base}/match/default",
                params={"api_key": self._settings.sanctions_api_key},
                json={"queries": queries},
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        except httpx.TimeoutException:
            return self._result(
                ProviderResultStatus.TIMEOUT,
                now,
                query_context,
                error=f"OpenSanctions API did not respond within {_DEFAULT_TIMEOUT_SECONDS:.0f}s.",
            )
        except httpx.HTTPError as exc:
            return self._result(
                ProviderResultStatus.ERROR, now, query_context, error=f"OpenSanctions API request failed: {exc}"
            )

        if response.status_code == 429:
            return self._result(
                ProviderResultStatus.RATE_LIMITED,
                now,
                query_context,
                error="OpenSanctions API quota reached (the trial is 50 requests/month; coverage for this check is INCOMPLETE).",
            )
        if response.status_code in (401, 403):
            return self._result(
                ProviderResultStatus.ERROR,
                now,
                query_context,
                error=f"OpenSanctions API rejected the key (HTTP {response.status_code}).",
            )
        if response.status_code >= 400:
            return self._result(
                ProviderResultStatus.ERROR,
                now,
                query_context,
                error=f"OpenSanctions API returned HTTP {response.status_code}: {response.text[:180]}",
            )

        try:
            payload = response.json()
        except ValueError:
            return self._result(
                ProviderResultStatus.ERROR, now, query_context, error="OpenSanctions API returned a non-JSON body."
            )

        candidates = self._map_results(payload.get("responses") or {}, now=now)
        status = ProviderResultStatus.SUCCESS if candidates else ProviderResultStatus.NO_RESULTS
        return self._result(status, now, query_context, items=candidates)

    def get_entity(self, external_id: str) -> ProviderResult[ExternalEntityCandidate]:
        # Not used on the screening path (search_entity is). Implemented for
        # protocol completeness; kept minimal to avoid spending quota on a code
        # path no monitoring cycle exercises.
        now = datetime.now(timezone.utc)
        if not self.is_configured():
            return self._result(
                ProviderResultStatus.NOT_CONFIGURED,
                now,
                {"external_id": external_id},
                error="opensanctions_match_api has no API key configured.",
            )
        return self._result(ProviderResultStatus.NO_RESULTS, now, {"external_id": external_id})

    # -- implementation -------------------------------------------------- #

    def _build_queries(self, name: str, *, country: str | None, entity_type: str | None) -> dict:
        props: dict = {"name": [name]}
        if country:
            props["country"] = [country]

        if entity_type:
            schema = _SCHEMA_FOR.get(entity_type.upper(), "LegalEntity")
            return {"q": {"schema": schema, "properties": props}}

        # Unknown type -> screen as both a person and an entity in one request.
        return {
            "q_person": {"schema": "Person", "properties": dict(props)},
            "q_entity": {"schema": "LegalEntity", "properties": dict(props)},
        }

    def _map_results(self, responses: dict, *, now: datetime) -> list[ExternalEntityCandidate]:
        seen: set[str] = set()
        out: list[ExternalEntityCandidate] = []
        for query_result in responses.values():
            for r in query_result.get("results") or []:
                ext_id = str(r.get("id") or "")
                if not ext_id or ext_id in seen:
                    continue
                seen.add(ext_id)
                props = r.get("properties") or {}
                out.append(
                    ExternalEntityCandidate(
                        provider=self.provider_name,
                        provider_kind=self.provider_kind,
                        source_tier=SourceTier.EXTERNAL_LIVE,
                        external_id=ext_id,
                        name=r.get("caption") or (props.get("name") or [""])[0],
                        aliases=list(props.get("alias") or []),
                        entity_type=r.get("schema"),
                        countries=list(props.get("country") or props.get("jurisdiction") or []),
                        nationalities=list(props.get("nationality") or []),
                        dates_of_birth=list(props.get("birthDate") or []),
                        identifiers=self._collect_identifiers(props),
                        # Carry the vendor score, match flag, matched lists and
                        # topics as PROVENANCE only -- never as confidence. The
                        # deterministic scorer decides that.
                        raw_source_reference=(
                            f"opensanctions:{ext_id} score={r.get('score')} match={r.get('match')} "
                            f"datasets={(r.get('datasets') or [])[:3]} topics={(props.get('topics') or [])[:4]}"
                        ),
                        retrieved_at=now,
                    )
                )
        return out

    @staticmethod
    def _collect_identifiers(props: dict) -> list[str]:
        out: list[str] = []
        for key in ("idNumber", "taxNumber", "registrationNumber", "passportNumber", "wikidataId"):
            out.extend(str(v) for v in (props.get(key) or []))
        return out

    def _result(
        self,
        status: ProviderResultStatus,
        now: datetime,
        query_context: dict,
        *,
        items: list[ExternalEntityCandidate] | None = None,
        error: str | None = None,
    ) -> ProviderResult[ExternalEntityCandidate]:
        return ProviderResult(
            status=status,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.SANCTIONS,
            items=items or [],
            error_message=error,
            queried_at=now,
            query_context=query_context,
        )
