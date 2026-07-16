"""
LocalCuratedAdverseMediaProvider -- a real AdverseMediaProvider that searches
the 3 adverse-media article fixtures (data/articles/*.txt) for mentions of a
queried name.

Reads the fixture files directly (like app/providers/local_sanctions_provider
.py), not through AdverseMediaArticle/ArticleRepository -- this keeps the
provider a stateless, registry-time-constructed singleton with no per-request
database session to manage, consistent with the existing Tier-2 sanctions
provider's design. The DB-backed AdverseMediaArticle table (populated by
app/ingestion/loaders/articles.py) exists for a different purpose: giving a
future phase a durable, queryable record of ingested articles. Re-reading 3
files totalling ~5 KB per call is cheap enough that this duplication of
access path is not a performance concern.

Uses rapidfuzz's partial_ratio (best-matching substring), which fits
"does this short name appear somewhere in this long article" better than a
whole-string comparison. Contains zero entity-specific logic -- runs the
identical scan for any input name, honestly returning NO_RESULTS for the
overwhelming majority of Phase 0 client names (see docs/phase-0-dataset-
audit.md SS1: the transaction/client demo and the media/UBO/sanctions demo
are two unconnected universes -- this provider does not pretend otherwise).
"""

from __future__ import annotations

from datetime import datetime, timezone

from rapidfuzz import fuzz

from app.core.config import Settings, get_settings
from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier
from app.providers.schemas import ExternalArticle, ProviderResult
from app.registry.sources import SourceRegistry

DEFAULT_MATCH_THRESHOLD = 75.0

_ARTICLE_SOURCE_KEYS = ("article_clean", "article_adverse_hit", "article_adversarial")


class LocalCuratedAdverseMediaProvider:
    """Implements the AdverseMediaProvider protocol (app/providers/contracts.py)."""

    provider_name = "local_curated_adverse_media_fixture"
    provider_kind = ProviderKind.LOCAL_REFERENCE_DATASET

    def __init__(
        self,
        settings: Settings | None = None,
        registry: SourceRegistry | None = None,
        match_threshold: float = DEFAULT_MATCH_THRESHOLD,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or SourceRegistry(self._settings)
        self._match_threshold = match_threshold

    def is_configured(self) -> bool:
        return any(self._registry.check_file_availability(key) for key in _ARTICLE_SOURCE_KEYS)

    def search_entity(self, name: str) -> ProviderResult[ExternalArticle]:
        now = datetime.now(timezone.utc)
        query_context = {"name": name}

        if not self.is_configured():
            return ProviderResult(
                status=ProviderResultStatus.NOT_CONFIGURED,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.ADVERSE_MEDIA,
                error_message="No adverse-media fixture files found on disk.",
                queried_at=now,
                query_context=query_context,
            )

        matches: list[ExternalArticle] = []
        try:
            for source_key in _ARTICLE_SOURCE_KEYS:
                source = self._registry.get_source(source_key)
                if source is None:
                    continue
                path = self._registry.resolve_path(source)
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8")
                score = fuzz.partial_ratio(name.lower(), text.lower())
                if score < self._match_threshold:
                    continue
                matches.append(
                    ExternalArticle(
                        provider=self.provider_name,
                        provider_kind=self.provider_kind,
                        source_tier=SourceTier.TIER_2_CURATED_DEMO,
                        external_id=path.name,
                        title=None,  # fixtures have no structured title field
                        source_name="local_curated_fixture",
                        publication_date=None,  # not present in the fixture text as structured data
                        url=None,
                        content_snippet=text[:280],
                        retrieved_at=now,
                    )
                )
        except Exception as exc:
            return ProviderResult(
                status=ProviderResultStatus.ERROR,
                provider=self.provider_name,
                provider_kind=self.provider_kind,
                category=ProviderCategory.ADVERSE_MEDIA,
                error_message=f"Failed to read article fixtures: {exc}",
                queried_at=now,
                query_context=query_context,
            )

        status = ProviderResultStatus.SUCCESS if matches else ProviderResultStatus.NO_RESULTS
        return ProviderResult(
            status=status,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.ADVERSE_MEDIA,
            items=matches,
            queried_at=now,
            query_context=query_context,
        )

    def fetch_recent_articles(
        self, name: str, *, since: datetime | None = None
    ) -> ProviderResult[ExternalArticle]:
        # The fixtures carry no reliable structured publication date to filter
        # on (see docs/data-dictionary.md) -- `since` is accepted for Protocol
        # compatibility but not applied. This is documented, not silently wrong.
        return self.search_entity(name)
