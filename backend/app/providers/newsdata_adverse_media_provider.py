"""
NewsdataAdverseMediaProvider -- a REAL, live AdverseMediaProvider that queries
newsdata.io for recent articles mentioning a name.

This is the first of the three external-API slots (news / sanctions / registry)
to graduate from placeholder to integration. The placeholder it replaces
(PendingAdverseMediaAPIProvider) made zero network calls by design and reported
NOT_CONFIGURED; this one makes a real HTTPS request when a key is configured and
maps the response into the same ExternalArticle envelope every other adverse-
media provider produces. The registry picks this class when NEWS_API_KEY is set
and falls back to the honest placeholder when it is not, so the "degrades
truthfully, never fabricates" contract holds at both ends.

Provenance is EXTERNAL_LIVE, never a curated tier: a live third-party article is
not the same class of evidence as a Tier-2 fixture, and the tier is what stops
one from being presented as the other.

Network failure is an expected outcome, not a crash. Every branch returns a
ProviderResult with an honest status (NOT_CONFIGURED / NO_RESULTS / RATE_LIMITED
/ TIMEOUT / ERROR), so a newsdata.io outage records weight-0 coverage rather
than raising a client's risk for our own failure.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.core.config import Settings, get_settings
from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier
from app.providers.schemas import ExternalArticle, ProviderResult

_DEFAULT_BASE_URL = "https://newsdata.io/api/1/latest"
_DEFAULT_TIMEOUT_SECONDS = 12.0
# newsdata.io returns at most 10 results per page on the free tier; we take one
# page. Screening wants "is this name in recent adverse media", not a full crawl.
_MAX_ARTICLES = 10


class NewsdataAdverseMediaProvider:
    """Implements the AdverseMediaProvider protocol (app/providers/contracts.py)."""

    provider_name = "newsdata_adverse_media_api"
    provider_kind = ProviderKind.EXTERNAL_API

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(self._settings.news_api_key)

    # -- protocol surface ------------------------------------------------ #

    def search_entity(self, name: str) -> ProviderResult[ExternalArticle]:
        return self._query(name, since=None)

    def fetch_recent_articles(
        self, name: str, *, since: datetime | None = None
    ) -> ProviderResult[ExternalArticle]:
        return self._query(name, since=since)

    # -- implementation -------------------------------------------------- #

    def _query(self, name: str, *, since: datetime | None) -> ProviderResult[ExternalArticle]:
        now = datetime.now(timezone.utc)
        query_context: dict = {"name": name, "since": since.isoformat() if since else None}

        if not self.is_configured():
            return self._result(
                ProviderResultStatus.NOT_CONFIGURED,
                now,
                query_context,
                error="newsdata_adverse_media_api has no API key configured (set NEWS_API_KEY to enable live news screening).",
            )

        base_url = self._settings.news_api_base_url or _DEFAULT_BASE_URL
        params = {
            "apikey": self._settings.news_api_key,
            # Quote the name so newsdata treats a multi-word name as a phrase
            # rather than an OR of its tokens -- the difference between
            # "articles about this entity" and "articles about anyone sharing a
            # word with its name".
            "q": f'"{name}"',
            "language": "en",
        }

        try:
            response = httpx.get(base_url, params=params, timeout=_DEFAULT_TIMEOUT_SECONDS)
        except httpx.TimeoutException:
            return self._result(
                ProviderResultStatus.TIMEOUT,
                now,
                query_context,
                error=f"newsdata.io did not respond within {_DEFAULT_TIMEOUT_SECONDS:.0f}s.",
            )
        except httpx.HTTPError as exc:  # DNS, connection, TLS -- all "we could not reach it"
            return self._result(
                ProviderResultStatus.ERROR, now, query_context, error=f"newsdata.io request failed: {exc}"
            )

        # 429 is its own honest state: coverage is incomplete because we are
        # throttled, not because the client is clean. The 50-req/mo trial hits
        # this quickly, so it must not be swallowed into a generic ERROR.
        if response.status_code == 429:
            return self._result(
                ProviderResultStatus.RATE_LIMITED,
                now,
                query_context,
                error="newsdata.io rate limit reached (the free tier is limited; coverage for this check is INCOMPLETE).",
            )
        if response.status_code >= 400:
            detail = self._extract_error(response)
            return self._result(
                ProviderResultStatus.ERROR,
                now,
                query_context,
                error=f"newsdata.io returned HTTP {response.status_code}: {detail}",
            )

        try:
            payload = response.json()
        except ValueError:
            return self._result(
                ProviderResultStatus.ERROR, now, query_context, error="newsdata.io returned a non-JSON body."
            )

        if payload.get("status") != "success":
            return self._result(
                ProviderResultStatus.ERROR,
                now,
                query_context,
                error=f"newsdata.io reported: {self._extract_error(response)}",
            )

        articles = self._map_articles(payload.get("results") or [], now=now, since=since)
        status = ProviderResultStatus.SUCCESS if articles else ProviderResultStatus.NO_RESULTS
        return self._result(status, now, query_context, items=articles)

    def _map_articles(
        self, raw: list[dict], *, now: datetime, since: datetime | None
    ) -> list[ExternalArticle]:
        out: list[ExternalArticle] = []
        for item in raw[:_MAX_ARTICLES]:
            published = self._parse_date(item.get("pubDate"))
            # A caller asking for articles "since" a date must not receive older
            # ones silently; drop them rather than mislabel the coverage window.
            if since is not None and published is not None and published < since:
                continue
            out.append(
                ExternalArticle(
                    provider=self.provider_name,
                    provider_kind=self.provider_kind,
                    source_tier=SourceTier.EXTERNAL_LIVE,
                    external_id=str(item.get("article_id") or item.get("link") or ""),
                    title=item.get("title"),
                    source_name=item.get("source_id") or item.get("source_name"),
                    publication_date=published,
                    url=item.get("link"),
                    content_snippet=item.get("description"),
                    retrieved_at=now,
                )
            )
        return out

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        # newsdata.io timestamps look like "2026-07-16 07:00:00" and are UTC.
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    @staticmethod
    def _extract_error(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.text[:200]
        results = body.get("results")
        if isinstance(results, dict):
            return str(results.get("message") or results.get("code") or results)
        return str(results or body)

    def _result(
        self,
        status: ProviderResultStatus,
        now: datetime,
        query_context: dict,
        *,
        items: list[ExternalArticle] | None = None,
        error: str | None = None,
    ) -> ProviderResult[ExternalArticle]:
        return ProviderResult(
            status=status,
            provider=self.provider_name,
            provider_kind=self.provider_kind,
            category=ProviderCategory.ADVERSE_MEDIA,
            items=items or [],
            error_message=error,
            queried_at=now,
            query_context=query_context,
        )
