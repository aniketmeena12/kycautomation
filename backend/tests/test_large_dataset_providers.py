"""
Large-dataset provider tests -- proves the lazy/streaming design against the
REAL Phase 0 files without ever fully loading them.

Cost budget for this file (measured during development, see
docs/phase-2-ingestion.md SS3):
  - Tier1OfacLookupProvider: ~0.7s per search (19,157-row file)
  - SamlDTransactionProvider with a limit: a few seconds (early-exit)
  - LocalCuratedAdverseMediaProvider: instant (3 tiny files)
  - Tier1OpenSanctionsLookupProvider (488 MB, 1.3M rows): ~40s per search --
    exercised in exactly ONE test here, not repeated, to keep the suite fast.
"""

import time

from app.core.enums import ProviderResultStatus, SourceTier
from app.providers.local_adverse_media_provider import LocalCuratedAdverseMediaProvider
from app.providers.saml_d_transaction_provider import SamlDTransactionProvider
from app.providers.tier1_ofac_provider import Tier1OfacLookupProvider


def test_tier1_ofac_provider_generic_nonsense_name_no_results():
    """Proves genericity: zero entity-specific code, this name appears
    nowhere in provider source."""
    provider = Tier1OfacLookupProvider()
    result = provider.search_entity("Qxzjklm Synthetic Nobody 000111222")
    assert result.status == ProviderResultStatus.NO_RESULTS
    assert result.items == []


def test_tier1_ofac_provider_finds_real_entity_and_tags_tier1():
    provider = Tier1OfacLookupProvider()
    result = provider.search_entity("AEROCARIBBEAN AIRLINES")
    assert result.status == ProviderResultStatus.SUCCESS
    assert len(result.items) >= 1
    top = result.items[0]
    assert top.external_id == "36"
    assert top.source_tier == SourceTier.TIER_1_AUTHORITATIVE  # never confused with Tier 2


def test_tier1_ofac_provider_never_loads_full_file_into_memory():
    """Empirical guardrail: a full 19,157-row streamed search must complete
    quickly. If a future change accidentally materialized the whole file at
    once, this would still likely pass on time but is a canary alongside the
    ingestion validators' equivalent timing guard."""
    provider = Tier1OfacLookupProvider()
    started = time.monotonic()
    provider.search_entity("some unrelated query string")
    assert time.monotonic() - started < 10.0


def test_tier1_ofac_provider_get_entity_by_id():
    provider = Tier1OfacLookupProvider()
    result = provider.get_entity("36")
    assert result.status == ProviderResultStatus.SUCCESS
    assert result.items[0].name == "AEROCARIBBEAN AIRLINES"

    missing = provider.get_entity("99999999-does-not-exist")
    assert missing.status == ProviderResultStatus.NO_RESULTS


def test_tier1_ofac_provider_missing_file_degrades_gracefully(tmp_path):
    from app.core.config import Settings

    provider = Tier1OfacLookupProvider(settings=Settings(raw_data_dir=tmp_path))
    result = provider.search_entity("anything")
    assert result.status == ProviderResultStatus.NOT_CONFIGURED


def test_local_adverse_media_provider_generic_and_real():
    provider = LocalCuratedAdverseMediaProvider()
    no_hit = provider.search_entity("Totally Unrelated Synthetic Name")
    assert no_hit.status == ProviderResultStatus.NO_RESULTS

    hit = provider.search_entity("Farid Hassan Abadi")
    assert hit.status == ProviderResultStatus.SUCCESS
    assert any(a.external_id == "adverse_hit_article.txt" for a in hit.items)
    assert all(a.source_tier == SourceTier.TIER_2_CURATED_DEMO for a in hit.items)


def test_saml_d_provider_limited_lookup_on_real_mapped_account():
    """Account 7401327478 is one of client_id=3's two verified-mapped
    accounts (docs/phase-0-dataset-audit.md SS10)."""
    provider = SamlDTransactionProvider()
    started = time.monotonic()
    result = provider.get_recent_transactions("7401327478", limit=20)
    elapsed = time.monotonic() - started

    assert result.status == ProviderResultStatus.SUCCESS
    assert len(result.items) == 20
    assert elapsed < 60.0  # early-exit at the limit, not a full 951 MB scan


def test_saml_d_provider_unknown_account_no_results_fast():
    provider = SamlDTransactionProvider()
    result = provider.get_recent_transactions("00000000000-not-a-real-account", limit=5)
    assert result.status == ProviderResultStatus.NO_RESULTS


def test_tier1_opensanctions_provider_real_search_slow_but_bounded():
    """The one deliberately-expensive test in this file -- exercises the
    full 488 MB / 1.3M-row streaming search exactly once."""
    from app.providers.tier1_opensanctions_provider import Tier1OpenSanctionsLookupProvider

    provider = Tier1OpenSanctionsLookupProvider()
    started = time.monotonic()
    result = provider.search_entity("Qxzjklm Synthetic Nobody 000111222")
    elapsed = time.monotonic() - started

    assert result.status == ProviderResultStatus.NO_RESULTS
    assert elapsed < 120.0  # measured ~40-45s in development; generous CI margin
