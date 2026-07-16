"""
Provider contracts, expressed as runtime-checkable Protocols rather than
inheritance-forcing ABCs -- this is deliberate: a provider only needs to
structurally satisfy the interface (duck typing), which keeps concrete
providers free to inherit from whatever base is convenient for them (e.g. a
shared HTTP-client mixin in a future phase) without fighting Python's single
inheritance.

Every method here is entity-agnostic by construction: they all take a plain
`name: str` (or an external ID) as a parameter. There is nothing in this
module that could reference a specific demo client, entity, or fixture --
implementations are what get exercised with real data, never the contracts.
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from app.core.enums import ProviderCategory, ProviderKind
from app.providers.schemas import ExternalArticle, ExternalEntityCandidate, ProviderResult


@runtime_checkable
class SanctionsProvider(Protocol):
    provider_name: str
    provider_kind: ProviderKind

    def is_configured(self) -> bool: ...

    def search_entity(
        self, name: str, *, country: str | None = None, entity_type: str | None = None
    ) -> ProviderResult[ExternalEntityCandidate]: ...

    def get_entity(self, external_id: str) -> ProviderResult[ExternalEntityCandidate]: ...


@runtime_checkable
class AdverseMediaProvider(Protocol):
    provider_name: str
    provider_kind: ProviderKind

    def is_configured(self) -> bool: ...

    def search_entity(self, name: str) -> ProviderResult[ExternalArticle]: ...

    def fetch_recent_articles(
        self, name: str, *, since: datetime | None = None
    ) -> ProviderResult[ExternalArticle]: ...


@runtime_checkable
class CorporateRegistryProvider(Protocol):
    provider_name: str
    provider_kind: ProviderKind

    def is_configured(self) -> bool: ...

    def search_company(
        self, name: str, *, country: str | None = None
    ) -> ProviderResult[ExternalEntityCandidate]: ...

    def get_company_changes(
        self, external_id: str, *, since: datetime | None = None
    ) -> ProviderResult[dict]: ...


@runtime_checkable
class TransactionProvider(Protocol):
    provider_name: str
    provider_kind: ProviderKind

    def is_configured(self) -> bool: ...

    def get_transactions(
        self, account_external_id: str, *, since: datetime | None = None
    ) -> ProviderResult[dict]: ...

    def get_recent_transactions(
        self, account_external_id: str, *, limit: int = 50
    ) -> ProviderResult[dict]: ...


@runtime_checkable
class OwnershipProvider(Protocol):
    provider_name: str
    provider_kind: ProviderKind

    def is_configured(self) -> bool: ...

    def get_ownership_graph(self, entity_external_id: str) -> ProviderResult[dict]: ...


# Which ProviderCategory each contract corresponds to -- used by the registry
# to validate that a provider registered under a category actually satisfies
# that category's contract.
CATEGORY_PROTOCOLS: dict[ProviderCategory, type] = {
    ProviderCategory.SANCTIONS: SanctionsProvider,
    ProviderCategory.ADVERSE_MEDIA: AdverseMediaProvider,
    ProviderCategory.CORPORATE_REGISTRY: CorporateRegistryProvider,
    ProviderCategory.TRANSACTION: TransactionProvider,
    ProviderCategory.OWNERSHIP: OwnershipProvider,
}
