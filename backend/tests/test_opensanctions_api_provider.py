"""
Tests for the live OpenSanctions match-API provider.

NO NETWORK -- and here that rule has teeth: the trial key is 50 requests/month,
so a single test that reached the real API would spend a reviewer's quota and be
non-deterministic besides. Every test injects a fake httpx response. The two
real calls this integration needed were made once, by hand, and recorded in the
phase notes; these prove the mapping, the provenance-not-confidence rule, and
the honest-status branches (esp. 429 -> RATE_LIMITED, which the trial hits).
"""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.core.enums import ProviderKind, ProviderResultStatus, SourceTier
from app.providers.opensanctions_api_provider import OpenSanctionsAPIProvider


def _provider(monkeypatch, *, status_code=200, json_body=None, raise_exc=None, key="oskey_test"):
    prov = OpenSanctionsAPIProvider(Settings(sanctions_api_key=key))

    def fake_post(url, params=None, json=None, timeout=None):
        if raise_exc is not None:
            raise raise_exc
        request = httpx.Request("POST", url)
        return httpx.Response(status_code, json=json_body if json_body is not None else {}, request=request)

    monkeypatch.setattr("app.providers.opensanctions_api_provider.httpx.post", fake_post)
    return prov


_ONE_HIT = {
    "responses": {
        "q": {
            "status": 200,
            "results": [
                {
                    "id": "Q7747",
                    "caption": "Vladimir Putin",
                    "score": 1.0,
                    "match": True,
                    "schema": "Person",
                    "datasets": ["ua_nsdc_sanctions", "fr_tresor_gels_avoir"],
                    "properties": {
                        "country": ["ru"],
                        "birthDate": ["1952-10-07"],
                        "topics": ["role.pep", "sanction"],
                        "alias": ["Putin"],
                    },
                }
            ],
        }
    }
}


def test_no_key_is_not_configured_and_makes_no_call(monkeypatch):
    def explode(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("network was attempted with no key configured")

    monkeypatch.setattr("app.providers.opensanctions_api_provider.httpx.post", explode)
    prov = OpenSanctionsAPIProvider(Settings(sanctions_api_key=None))
    result = prov.search_entity("Anyone")
    assert result.status is ProviderResultStatus.NOT_CONFIGURED
    assert result.items == []


def test_success_maps_candidate_with_external_live_tier(monkeypatch):
    prov = _provider(monkeypatch, json_body=_ONE_HIT)
    result = prov.search_entity("Vladimir Putin", entity_type="PERSON")
    assert result.status is ProviderResultStatus.SUCCESS
    assert len(result.items) == 1
    c = result.items[0]
    assert c.source_tier is SourceTier.EXTERNAL_LIVE
    assert c.provider_kind is ProviderKind.EXTERNAL_API
    assert c.external_id == "Q7747"
    assert c.name == "Vladimir Putin"
    assert c.countries == ["ru"]
    assert c.dates_of_birth == ["1952-10-07"]


def test_vendor_score_is_provenance_not_confidence(monkeypatch):
    # The vendor ships score=1.0 and match=True. Those must NOT become the
    # authoritative signal -- they are recorded as text for the audit trail and
    # the deterministic scorer decides confidence. The candidate schema has no
    # score/confidence field at all, which is the structural guarantee.
    prov = _provider(monkeypatch, json_body=_ONE_HIT)
    c = prov.search_entity("Vladimir Putin", entity_type="PERSON").items[0]
    assert not hasattr(c, "score")
    assert not hasattr(c, "confidence")
    assert "score=1.0" in (c.raw_source_reference or "")
    assert "match=True" in (c.raw_source_reference or "")


def test_batched_person_and_entity_results_are_deduped(monkeypatch):
    # With no entity_type, the provider sends a Person and a LegalEntity query in
    # one request; the same id appearing in both must collapse to one candidate.
    body = {
        "responses": {
            "q_person": {"results": [dict(_ONE_HIT["responses"]["q"]["results"][0])]},
            "q_entity": {"results": [dict(_ONE_HIT["responses"]["q"]["results"][0])]},
        }
    }
    prov = _provider(monkeypatch, json_body=body)
    result = prov.search_entity("Vladimir Putin")
    assert len(result.items) == 1


def test_empty_results_is_no_results(monkeypatch):
    prov = _provider(monkeypatch, json_body={"responses": {"q": {"results": []}}})
    assert prov.search_entity("Nobody Clean").status is ProviderResultStatus.NO_RESULTS


def test_http_429_maps_to_rate_limited(monkeypatch):
    # The 50/mo trial hits this; it must read as INCOMPLETE coverage, not ERROR.
    prov = _provider(monkeypatch, status_code=429, json_body={})
    assert prov.search_entity("Anyone").status is ProviderResultStatus.RATE_LIMITED


def test_bad_key_maps_to_error(monkeypatch):
    prov = _provider(monkeypatch, status_code=403, json_body={})
    assert prov.search_entity("Anyone").status is ProviderResultStatus.ERROR


def test_timeout_maps_to_timeout(monkeypatch):
    prov = _provider(monkeypatch, raise_exc=httpx.TimeoutException("slow"))
    assert prov.search_entity("Anyone").status is ProviderResultStatus.TIMEOUT
