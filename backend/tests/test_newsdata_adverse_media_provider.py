"""
Tests for the live newsdata.io adverse-media provider.

NO NETWORK. Every test injects a fake httpx response, exactly as the LLM tests
inject a fake model. A test that hit the real newsdata.io would burn quota, be
non-deterministic, and -- per ADR-031 -- is not a test but a live verification.
The one real call this integration needs was made once, by hand, and recorded
in the phase notes; here we prove the mapping and the honest-status branches.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.core.config import Settings
from app.core.enums import ProviderKind, ProviderResultStatus, SourceTier
from app.providers.newsdata_adverse_media_provider import NewsdataAdverseMediaProvider


def _provider(monkeypatch, *, status_code=200, json_body=None, raise_exc=None, key="pub_test"):
    prov = NewsdataAdverseMediaProvider(Settings(news_api_key=key))

    def fake_get(url, params=None, timeout=None):
        if raise_exc is not None:
            raise raise_exc
        request = httpx.Request("GET", url)
        return httpx.Response(status_code, json=json_body if json_body is not None else {}, request=request)

    monkeypatch.setattr("app.providers.newsdata_adverse_media_provider.httpx.get", fake_get)
    return prov


_ONE_ARTICLE = {
    "status": "success",
    "totalResults": 1,
    "results": [
        {
            "article_id": "abc123",
            "title": "Firm probed over sanctions breach",
            "link": "https://example.com/a",
            "description": "A snippet.",
            "pubDate": "2026-07-15 19:39:22",
            "source_id": "news18",
        }
    ],
}


def test_no_key_is_not_configured_and_makes_no_call(monkeypatch):
    # No fake needed: the provider must short-circuit before touching httpx.
    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("network was attempted with no key configured")

    monkeypatch.setattr("app.providers.newsdata_adverse_media_provider.httpx.get", explode)
    prov = NewsdataAdverseMediaProvider(Settings(news_api_key=None))
    result = prov.search_entity("Anyone")
    assert result.status is ProviderResultStatus.NOT_CONFIGURED
    assert result.items == []


def test_success_maps_articles_with_external_live_tier(monkeypatch):
    prov = _provider(monkeypatch, json_body=_ONE_ARTICLE)
    result = prov.search_entity("Some Corp")
    assert result.status is ProviderResultStatus.SUCCESS
    assert len(result.items) == 1
    a = result.items[0]
    # A live third-party article is EXTERNAL_LIVE -- never a curated tier that
    # could let it pose as authoritative reference data.
    assert a.source_tier is SourceTier.EXTERNAL_LIVE
    assert a.provider_kind is ProviderKind.EXTERNAL_API
    assert a.title == "Firm probed over sanctions breach"
    assert a.url == "https://example.com/a"
    assert a.publication_date == datetime(2026, 7, 15, 19, 39, 22, tzinfo=timezone.utc)


def test_empty_results_is_no_results_not_error(monkeypatch):
    prov = _provider(monkeypatch, json_body={"status": "success", "totalResults": 0, "results": []})
    assert prov.search_entity("Nobody").status is ProviderResultStatus.NO_RESULTS


def test_http_429_maps_to_rate_limited(monkeypatch):
    # The 50-req/mo trial hits this fast; it must be its own state, not ERROR.
    prov = _provider(monkeypatch, status_code=429, json_body={"status": "error"})
    assert prov.search_entity("Some Corp").status is ProviderResultStatus.RATE_LIMITED


def test_timeout_maps_to_timeout(monkeypatch):
    prov = _provider(monkeypatch, raise_exc=httpx.TimeoutException("slow"))
    assert prov.search_entity("Some Corp").status is ProviderResultStatus.TIMEOUT


def test_transport_error_maps_to_error(monkeypatch):
    prov = _provider(monkeypatch, raise_exc=httpx.ConnectError("dns"))
    assert prov.search_entity("Some Corp").status is ProviderResultStatus.ERROR


def test_since_filter_drops_older_articles(monkeypatch):
    prov = _provider(monkeypatch, json_body=_ONE_ARTICLE)
    # The single fixture article is from 2026-07-15; asking for articles since
    # 2026-07-16 must return none rather than mislabel the coverage window.
    since = datetime(2026, 7, 16, tzinfo=timezone.utc)
    result = prov.fetch_recent_articles("Some Corp", since=since)
    assert result.status is ProviderResultStatus.NO_RESULTS
