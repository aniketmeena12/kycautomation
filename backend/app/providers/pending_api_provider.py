"""
Honest placeholders for the three planned external API integrations (news/
adverse-media, sanctions, corporate registry). These are NOT fake
implementations that pretend to call an API -- they make zero network calls,
ever. They exist so that:

  1. app/providers/registry.py has something real to register under each
     external-facing category, proving providers can be registered/configured
     dynamically (see architecture requirement 3) before any real HTTP client
     code exists.
  2. The NOT_CONFIGURED degradation path (architecture requirement 7) is
     exercised by real code and covered by a real test, not just documented.
  3. A future phase implementing the real integration has an exact class to
     replace, with the config plumbing (Settings fields, .env.example
     entries) already wired.

If a future developer sets the relevant API key in the environment, these
classes report ERROR with an explicit "not yet implemented" message rather
than fabricating a response -- see docs/phase-1-foundation.md's "DATA IS
DATA, NOT INSTRUCTIONS" / no-fake-functionality principle.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import Settings, get_settings
from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus
from app.providers.schemas import ExternalArticle, ExternalEntityCandidate, ProviderResult


class _PendingAPIProviderBase:
    """Shared plumbing for a not-yet-implemented external API provider.
    Concrete category-specific classes below just supply provider_name,
    category, and which Settings field holds the API key."""

    provider_kind = ProviderKind.EXTERNAL_API

    def __init__(
        self,
        provider_name: str,
        category: ProviderCategory,
        api_key_attr: str,
        settings: Settings | None = None,
    ) -> None:
        self.provider_name = provider_name
        self._category = category
        self._api_key_attr = api_key_attr
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(getattr(self._settings, self._api_key_attr, None))

    def _result(self, *, query_context: dict) -> ProviderResult:
        now = datetime.now(timezone.utc)
        if not self.is_configured():
            return ProviderResult(
                status=ProviderResultStatus.NOT_CONFIGURED,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=self._category,
                error_message=f"{self.provider_name} has no API key configured "
                f"(set {self._api_key_attr.upper()} to enable this integration in a future phase).",
                queried_at=now,
                query_context=query_context,
            )
        # A key IS configured, but the real HTTP integration has not been
        # built yet (Phase 1 scope is contracts only). Report this honestly
        # rather than fabricating a response.
        return ProviderResult(
            status=ProviderResultStatus.ERROR,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=self._category,
            error_message=f"{self.provider_name} is configured but its live integration "
            "is not implemented yet (deferred to a future phase).",
            queried_at=now,
            query_context=query_context,
        )


class PendingSanctionsAPIProvider(_PendingAPIProviderBase):
    """Satisfies the SanctionsProvider protocol. See module docstring."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__("pending_sanctions_api", ProviderCategory.SANCTIONS, "sanctions_api_key", settings)

    def search_entity(
        self, name: str, *, country: str | None = None, entity_type: str | None = None
    ) -> ProviderResult[ExternalEntityCandidate]:
        return self._result(query_context={"name": name, "country": country, "entity_type": entity_type})

    def get_entity(self, external_id: str) -> ProviderResult[ExternalEntityCandidate]:
        return self._result(query_context={"external_id": external_id})


class PendingAdverseMediaAPIProvider(_PendingAPIProviderBase):
    """Satisfies the AdverseMediaProvider protocol. See module docstring."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__("pending_news_api", ProviderCategory.ADVERSE_MEDIA, "news_api_key", settings)

    def search_entity(self, name: str) -> ProviderResult[ExternalArticle]:
        return self._result(query_context={"name": name})

    def fetch_recent_articles(
        self, name: str, *, since: datetime | None = None
    ) -> ProviderResult[ExternalArticle]:
        return self._result(query_context={"name": name, "since": since.isoformat() if since else None})


class PendingCorporateRegistryProvider(_PendingAPIProviderBase):
    """Satisfies the CorporateRegistryProvider protocol. See module docstring."""

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__(
            "pending_corporate_registry_api",
            ProviderCategory.CORPORATE_REGISTRY,
            "corporate_registry_api_key",
            settings,
        )

    def search_company(
        self, name: str, *, country: str | None = None
    ) -> ProviderResult[ExternalEntityCandidate]:
        return self._result(query_context={"name": name, "country": country})

    def get_company_changes(self, external_id: str, *, since: datetime | None = None) -> ProviderResult[dict]:
        return self._result(
            query_context={"external_id": external_id, "since": since.isoformat() if since else None}
        )
