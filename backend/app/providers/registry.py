"""
ProviderRegistry -- runtime registry of data providers by category.

This is the dependency-injection point the architecture update calls for:
services in a future phase ask the registry for "the SANCTIONS providers"
and get back whatever is currently registered (today: one local fixture
provider and one honest not-yet-configured API placeholder), without caring
how many there are or where they came from. Registering a new provider --
local or live -- never requires touching calling code.

A category may have multiple providers (the hybrid design: query local
reference data AND a live API for the same category, then merge). Order is
registration order; callers decide how to combine results.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import ProviderCategory, ProviderKind
from app.providers.contracts import CATEGORY_PROTOCOLS
from app.providers.local_adverse_media_provider import LocalCuratedAdverseMediaProvider
from app.providers.local_sanctions_provider import LocalCuratedSanctionsProvider
from app.providers.pending_api_provider import (
    PendingAdverseMediaAPIProvider,
    PendingCorporateRegistryProvider,
    PendingSanctionsAPIProvider,
)
from app.providers.saml_d_transaction_provider import SamlDTransactionProvider
from app.providers.tier1_ofac_provider import Tier1OfacLookupProvider
from app.providers.tier1_opensanctions_provider import Tier1OpenSanctionsLookupProvider


@dataclass
class ProviderMetadata:
    provider_name: str
    provider_kind: ProviderKind
    category: ProviderCategory
    configured: bool


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[ProviderCategory, list[object]] = {c: [] for c in ProviderCategory}

    def register(self, category: ProviderCategory, provider: object) -> None:
        expected_protocol = CATEGORY_PROTOCOLS.get(category)
        if expected_protocol is not None and not isinstance(provider, expected_protocol):
            raise TypeError(
                f"Provider {provider!r} does not satisfy the {expected_protocol.__name__} "
                f"protocol required for category {category}."
            )
        self._providers[category].append(provider)

    def get_providers(self, category: ProviderCategory) -> list[object]:
        return list(self._providers.get(category, []))

    def list_all(self) -> list[ProviderMetadata]:
        result: list[ProviderMetadata] = []
        for category, providers in self._providers.items():
            for provider in providers:
                result.append(
                    ProviderMetadata(
                        provider_name=provider.provider_name,
                        provider_kind=provider.provider_kind,
                        category=category,
                        configured=provider.is_configured(),
                    )
                )
        return result


def build_default_registry() -> ProviderRegistry:
    """The registry the application actually uses at runtime. Adding a new
    provider -- local or a real future API integration -- is exactly one
    `registry.register(...)` call here; nothing else in the app changes.

    SANCTIONS now demonstrates the full hybrid design in one category: a
    Tier-2 curated local fixture, a Tier-1 authoritative local (lazy,
    streaming) lookup, and a not-yet-implemented external API placeholder,
    all queryable through the identical ProviderResult[ExternalEntityCandidate]
    contract -- see docs/phase-2-ingestion.md SS3."""
    registry = ProviderRegistry()
    registry.register(ProviderCategory.SANCTIONS, LocalCuratedSanctionsProvider())
    registry.register(ProviderCategory.SANCTIONS, Tier1OfacLookupProvider())
    registry.register(ProviderCategory.SANCTIONS, Tier1OpenSanctionsLookupProvider())
    registry.register(ProviderCategory.SANCTIONS, PendingSanctionsAPIProvider())
    registry.register(ProviderCategory.ADVERSE_MEDIA, LocalCuratedAdverseMediaProvider())
    registry.register(ProviderCategory.ADVERSE_MEDIA, PendingAdverseMediaAPIProvider())
    registry.register(ProviderCategory.CORPORATE_REGISTRY, PendingCorporateRegistryProvider())
    registry.register(ProviderCategory.TRANSACTION, SamlDTransactionProvider())
    return registry


_default_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    """FastAPI dependency / module-level accessor for the shared registry."""
    global _default_registry
    if _default_registry is None:
        _default_registry = build_default_registry()
    return _default_registry
