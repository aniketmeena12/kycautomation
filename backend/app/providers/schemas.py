"""
Normalized external-data contracts.

Every provider -- whether it reads a local CSV or (in a future phase) calls a
live HTTP API -- returns data shaped like these models. Nothing downstream of
a provider call needs to know or care whether a candidate came from a 20-row
curated fixture or a real sanctions API; it only sees an ExternalEntityCandidate
or ExternalArticle with full provenance attached.

This is what "provider responses must be normalized" (architecture
requirement 5) means concretely: no provider-specific response shape ever
leaks past app/providers/.
"""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier

T = TypeVar("T")


class ExternalEntityCandidate(BaseModel):
    """A normalized sanctions/watchlist/PEP/corporate-registry candidate,
    regardless of which provider produced it."""

    provider: str
    provider_kind: ProviderKind
    source_tier: SourceTier
    external_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    entity_type: str | None = None
    countries: list[str] = Field(default_factory=list)
    nationalities: list[str] = Field(default_factory=list)
    dates_of_birth: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)
    raw_source_reference: str | None = None
    retrieved_at: datetime


class ExternalArticle(BaseModel):
    """A normalized adverse-media article, regardless of which provider
    produced it."""

    provider: str
    provider_kind: ProviderKind
    source_tier: SourceTier
    external_id: str
    title: str | None = None
    source_name: str | None = None
    publication_date: datetime | None = None
    url: str | None = None
    content_snippet: str | None = None
    retrieved_at: datetime


class ProviderResult(BaseModel, Generic[T]):
    """Uniform envelope for every provider call. `status` must always be
    checked before trusting `items` -- a non-SUCCESS status is not an
    exception, it's an expected, handled outcome (see architecture
    requirement 7: provider unavailability must not break the system)."""

    status: ProviderResultStatus
    provider: str
    provider_kind: ProviderKind
    category: ProviderCategory
    items: list[T] = Field(default_factory=list)
    error_message: str | None = None
    queried_at: datetime
    query_context: dict[str, Any] = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (ProviderResultStatus.SUCCESS, ProviderResultStatus.NO_RESULTS)
