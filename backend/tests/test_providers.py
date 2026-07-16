"""
Provider architecture tests -- verifying the anti-hardcoding design.

These prove the provider layer is entity-agnostic: the same
LocalCuratedSanctionsProvider code path handles an arbitrary nonsense name
and a name that happens to match a demo fixture identically, with no
special-casing anywhere in app/providers/. The demo fixture name used below
appears only in this test's assertions, never in any provider source file.
"""

from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus, SourceTier
from app.providers.local_sanctions_provider import LocalCuratedSanctionsProvider
from app.providers.pending_api_provider import PendingAdverseMediaAPIProvider, PendingSanctionsAPIProvider
from app.providers.registry import ProviderRegistry, build_default_registry


def test_local_provider_returns_no_results_for_arbitrary_unknown_name():
    """Proves genericity: this string exists nowhere in any fixture or in
    provider source code, yet the call succeeds and degrades cleanly."""
    provider = LocalCuratedSanctionsProvider()
    result = provider.search_entity("Qxzjklm Synthetic Nobody 000111222")
    assert result.status == ProviderResultStatus.NO_RESULTS
    assert result.items == []


def test_local_provider_finds_a_real_curated_entity_generically():
    """Proves the same, unmodified code path actually works when given a
    name that happens to be in the curated fixture -- the provider takes
    `name` as a plain argument, it does not special-case this or any other
    value."""
    provider = LocalCuratedSanctionsProvider()
    result = provider.search_entity("Mohammad Al-Rashid")
    assert result.status == ProviderResultStatus.SUCCESS
    assert len(result.items) >= 1
    candidate = result.items[0]
    assert candidate.source_tier == SourceTier.TIER_2_CURATED_DEMO
    assert candidate.provider_kind == ProviderKind.LOCAL_REFERENCE_DATASET


def test_local_provider_every_result_carries_full_provenance():
    provider = LocalCuratedSanctionsProvider()
    result = provider.search_entity("Farid Hassan Abadi")
    assert result.status == ProviderResultStatus.SUCCESS
    for candidate in result.items:
        assert candidate.provider == provider.provider_name
        assert candidate.source_tier == SourceTier.TIER_2_CURATED_DEMO
        assert candidate.external_id
        assert candidate.retrieved_at is not None


def test_local_provider_missing_file_degrades_gracefully(tmp_path):
    from app.core.config import Settings

    fake_settings = Settings(raw_data_dir=tmp_path)  # empty dir, no fixture files
    provider = LocalCuratedSanctionsProvider(settings=fake_settings)
    result = provider.search_entity("anything at all")
    assert result.status == ProviderResultStatus.NOT_CONFIGURED
    assert result.items == []


def test_pending_api_provider_is_not_configured_by_default():
    """No API key is set anywhere in this environment -- the graceful
    degradation path must be what actually runs, not a mocked stand-in."""
    provider = PendingSanctionsAPIProvider()
    assert provider.is_configured() is False

    result = provider.search_entity("any entity name")
    assert result.status == ProviderResultStatus.NOT_CONFIGURED
    assert result.items == []
    assert "SANCTIONS_API_KEY" in result.error_message


def test_pending_provider_never_raises_regardless_of_input():
    provider = PendingAdverseMediaAPIProvider()
    for name in ["", "a" * 500, "unicode éè name", "SELECT * FROM clients;"]:
        result = provider.search_entity(name)
        assert result.status == ProviderResultStatus.NOT_CONFIGURED


def test_provider_registry_allows_dynamic_registration():
    """A brand-new, previously-unknown provider can be registered at
    runtime and immediately shows up -- the registry does not hardcode a
    fixed provider list."""

    class DummySanctionsProvider:
        provider_name = "dummy_test_provider"
        provider_kind = ProviderKind.EXTERNAL_API

        def is_configured(self) -> bool:
            return True

        def search_entity(self, name, *, country=None, entity_type=None):
            raise NotImplementedError

        def get_entity(self, external_id):
            raise NotImplementedError

    registry = ProviderRegistry()
    registry.register(ProviderCategory.SANCTIONS, DummySanctionsProvider())

    names = {m.provider_name for m in registry.list_all()}
    assert "dummy_test_provider" in names


def test_provider_registry_rejects_provider_missing_required_methods():
    class NotAProvider:
        provider_name = "broken"
        provider_kind = ProviderKind.EXTERNAL_API
        # missing is_configured / search_entity / get_entity

    registry = ProviderRegistry()
    import pytest

    with pytest.raises(TypeError):
        registry.register(ProviderCategory.SANCTIONS, NotAProvider())


def test_default_registry_has_local_and_external_providers_per_category():
    """Confirms the hybrid design end-to-end: the SANCTIONS category has
    both a LOCAL_REFERENCE_DATASET provider and an EXTERNAL_API provider
    registered simultaneously, with distinct provenance kinds."""
    registry = build_default_registry()
    sanctions_providers = registry.get_providers(ProviderCategory.SANCTIONS)
    kinds = {p.provider_kind for p in sanctions_providers}
    assert ProviderKind.LOCAL_REFERENCE_DATASET in kinds
    assert ProviderKind.EXTERNAL_API in kinds


def test_providers_endpoint_never_leaks_api_keys(client, monkeypatch):
    monkeypatch.setenv("SANCTIONS_API_KEY", "super-secret-value-should-never-appear")
    from app.core.config import get_settings

    get_settings.cache_clear()

    r = client.get("/api/v1/providers")
    assert r.status_code == 200
    assert "super-secret-value-should-never-appear" not in r.text

    get_settings.cache_clear()  # restore for subsequent tests
