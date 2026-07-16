"""
Read-only external/local data provider status API.

Proves the provider architecture (app/providers/) is real and inspectable:
which providers are registered per category, whether each is configured, and
what kind it is (INTERNAL_DATASET / LOCAL_REFERENCE_DATASET / EXTERNAL_API).
Never exposes an API key or any other secret -- only provider_name,
provider_kind, category, and a boolean `configured`.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_provider_registry
from app.core.enums import ProviderCategory, ProviderKind
from app.providers.registry import ProviderRegistry

router = APIRouter(prefix="/providers", tags=["providers"])


class ProviderStatusRead(BaseModel):
    provider_name: str
    provider_kind: ProviderKind
    category: ProviderCategory
    configured: bool


class ProviderStatusListResponse(BaseModel):
    providers: list[ProviderStatusRead]
    total: int
    configured_count: int


@router.get("", response_model=ProviderStatusListResponse)
def list_providers(
    registry: ProviderRegistry = Depends(get_provider_registry),
) -> ProviderStatusListResponse:
    providers = [
        ProviderStatusRead(
            provider_name=m.provider_name,
            provider_kind=m.provider_kind,
            category=m.category,
            configured=m.configured,
        )
        for m in registry.list_all()
    ]
    return ProviderStatusListResponse(
        providers=providers,
        total=len(providers),
        configured_count=sum(1 for p in providers if p.configured),
    )
