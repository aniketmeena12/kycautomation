"""Dataset source registry: loads, distinguishes Tier 1 from Tier 2, excludes
out-of-scope privacy datasets, and handles missing sources safely."""

import tempfile
from pathlib import Path

from app.core.config import Settings
from app.core.enums import SourceTier
from app.registry.sources import SourceRegistry


def test_registry_loads_all_expected_sources():
    registry = SourceRegistry()
    keys = {s.source_key for s in registry.list_sources()}
    assert "clients" in keys
    assert "ofac_sdn" in keys
    assert "sample_ofac_sdn" in keys
    assert len(keys) == 16


def test_out_of_scope_privacy_datasets_are_never_registered():
    registry = SourceRegistry()
    keys = {s.source_key for s in registry.list_sources()}
    paths = {s.relative_path for s in registry.list_sources()}
    for forbidden in ("opp115", "privacy_qa", "gdpr", "gcapi"):
        assert not any(forbidden in k for k in keys)
        assert not any(forbidden in p for p in paths)


def test_tier1_vs_tier2_sanctions_distinction():
    registry = SourceRegistry()
    ofac_sdn = registry.get_source("ofac_sdn")
    sample_ofac_sdn = registry.get_source("sample_ofac_sdn")

    assert ofac_sdn.source_tier == SourceTier.TIER_1_AUTHORITATIVE
    assert sample_ofac_sdn.source_tier == SourceTier.TIER_2_CURATED_DEMO
    assert ofac_sdn.source_tier != sample_ofac_sdn.source_tier


def test_registry_reflects_real_data_directory():
    """The real project data/ directory should show every registered source
    as available -- this is an integration check against actual Phase 0 data,
    not a mock."""
    registry = SourceRegistry()
    for source in registry.list_sources():
        assert registry.check_file_availability(
            source.source_key
        ), f"{source.source_key} not found at {registry.resolve_path(source)}"


def test_unknown_source_key_handled_safely():
    registry = SourceRegistry()
    assert registry.get_source("totally_made_up_key") is None
    assert registry.check_file_availability("totally_made_up_key") is False


def test_missing_file_reported_without_crashing():
    """Point the registry at an empty temp directory -- every source should
    report unavailable, not raise."""
    with tempfile.TemporaryDirectory() as tmp:
        fake_settings = Settings(raw_data_dir=Path(tmp))
        registry = SourceRegistry(settings=fake_settings)
        for source in registry.list_sources():
            assert registry.check_file_availability(source.source_key) is False


def test_sources_endpoint_exposes_provenance_without_absolute_paths(client):
    r = client.get("/api/v1/sources")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 16

    by_key = {s["source_key"]: s for s in body["sources"]}
    assert by_key["ofac_sdn"]["source_tier"] == "TIER_1_AUTHORITATIVE"
    assert by_key["sample_ofac_sdn"]["source_tier"] == "TIER_2_CURATED_DEMO"

    for s in body["sources"]:
        # relative_path only -- never a resolved OS-specific absolute path
        assert not s["relative_path"].startswith("/")
        assert ":" not in s["relative_path"][:3]  # no "C:\" style prefix

    r404 = client.get("/api/v1/sources/does-not-exist")
    assert r404.status_code == 404
