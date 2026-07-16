"""
Dataset source registry -- static, code-defined metadata for every in-scope
Phase 0 dataset (docs/phase-0-dataset-audit.md SS3). This is deliberately
plain Python, not a database table: it doesn't change at runtime, and
inspecting it must never touch disk beyond a single Path.exists() check per
source (see SourceRegistry.check_file_availability).

Design rules enforced here, straight from docs/phase-0-dataset-audit.md and
the Phase 1 brief:

  1. Canonical paths only. The duplicate root-level copies of
     clients_with_fatf_ofac.csv / transactions_with_fatf_ofac.csv (verified
     byte-identical to the kyc_profiles/ copies) are NOT registered --
     registering both would let a future ingestion job double-count records.
  2. Tier 1 (ofac_sdn/alt/add.csv, opensanctions_targets.csv) and Tier 2
     (sample_ofac_sdn/alt.csv, sample_opensanctions.csv) are separate,
     explicitly tagged registry entries -- never one merged "sanctions"
     source.
  3. The out-of-scope privacy/GDPR corpus (opp115/, privacy_qa/, gdpr*,
     gcapi.dll) is never registered here at all -- it isn't filtered out of
     a bigger list, it was never a KYC source to begin with.
  4. Every large or Tier-1-authoritative file (SAML-D.csv, ofac_sdn.csv,
     ofac_alt.csv, ofac_add.csv, opensanctions_targets.csv) is registered
     with an ingestion_strategy of LOOKUP_ONLY -- there is no FULL_LOAD
     strategy anywhere in this file for anything over a few MB. Phase 2
     implements these as streaming/chunked provider lookups
     (app/providers/tier1_ofac_provider.py, tier1_opensanctions_provider.py,
     saml_d_transaction_provider.py) that never persist bulk rows to SQLite
     -- see docs/phase-2-ingestion.md SS3.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.enums import IngestionStrategy, SourceCategory, SourceFormat, SourceTier, SourceType


@dataclass(frozen=True)
class SourceDefinition:
    source_key: str
    display_name: str
    relative_path: str  # relative to settings.raw_data_dir
    category: SourceCategory
    source_tier: SourceTier
    source_type: SourceType
    format: SourceFormat
    known_record_count: int | None  # approximate, measured in docs/phase-0-dataset-audit.md
    enabled: bool
    ingestion_strategy: IngestionStrategy
    description: str
    has_header: bool = True
    expected_columns: tuple[str, ...] | None = None


# Column names for the three headerless Tier-1 OFAC files were recovered from
# the matching Tier-2 sample file headers -- see docs/data-dictionary.md.
_OFAC_SDN_COLUMNS = (
    "ent_num",
    "SDN_Name",
    "SDN_Type",
    "Program",
    "Title",
    "Call_Sign",
    "Vess_type",
    "Tonnage",
    "GRT",
    "Vess_flag",
    "Vess_owner",
    "Remarks",
)
_OFAC_ALT_COLUMNS = ("ent_num", "alt_num", "alt_type", "alt_name", "alt_remarks")
_OFAC_ADD_COLUMNS = ("ent_num", "add_num", "address", "city_state_zip", "country", "add_remarks")

DATASET_SOURCES: tuple[SourceDefinition, ...] = (
    SourceDefinition(
        source_key="clients",
        display_name="KYC Client Master",
        relative_path="kyc_profiles/clients_with_fatf_ofac.csv",
        category=SourceCategory.CLIENT_MASTER,
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
        format=SourceFormat.CSV,
        known_record_count=2000,
        enabled=True,
        ingestion_strategy=IngestionStrategy.FULL_LOAD,
        description="Primary monitored-entity roster. 2,000 clients, unique client_id.",
    ),
    SourceDefinition(
        source_key="client_account_mapping",
        display_name="Client-to-Account Mapping",
        relative_path="kyc_profiles/client_account_mapping.csv",
        category=SourceCategory.ACCOUNT_MAPPING,
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
        format=SourceFormat.CSV,
        known_record_count=120,
        enabled=True,
        ingestion_strategy=IngestionStrategy.FULL_LOAD,
        description="Bridges 60 of 2,000 clients to 120 accounts, all verified present in SAML-D.",
    ),
    SourceDefinition(
        source_key="transactions_shallow",
        display_name="Shallow KYC Transactions (all clients)",
        relative_path="kyc_profiles/transactions_with_fatf_ofac.csv",
        category=SourceCategory.TRANSACTION,
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
        format=SourceFormat.CSV,
        known_record_count=50000,
        enabled=True,
        ingestion_strategy=IngestionStrategy.FULL_LOAD,
        description="Pre-flagged transactions covering all 2,000 clients, 2025-07 to 2025-09.",
    ),
    SourceDefinition(
        source_key="saml_d",
        display_name="SAML-D Deep AML Transaction History",
        relative_path="aml_transactions/SAML-D.csv",
        category=SourceCategory.TRANSACTION,
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
        format=SourceFormat.CSV,
        known_record_count=9_504_852,
        enabled=True,
        ingestion_strategy=IngestionStrategy.LOOKUP_ONLY,
        description=(
            "951 MB, 9.5M rows, labelled ground-truth laundering data. NEVER fully loaded -- "
            "Phase 2 provides SamlDTransactionProvider, a streaming lookup scoped to a single "
            "account's rows at a time, never a bulk load into SQLite."
        ),
    ),
    SourceDefinition(
        source_key="ofac_sdn",
        display_name="OFAC SDN List (Tier 1, authoritative)",
        relative_path="sanctions/ofac_sdn.csv",
        category=SourceCategory.SANCTIONS_LIST,
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        source_type=SourceType.OFAC_SDN,
        format=SourceFormat.CSV,
        known_record_count=19157,
        enabled=True,
        ingestion_strategy=IngestionStrategy.LOOKUP_ONLY,
        description=(
            "Real, full-scale OFAC Specially Designated Nationals list. No header row. Phase 2 "
            "provides Tier1OfacLookupProvider -- chunked streaming fuzzy search, never bulk-loaded."
        ),
        has_header=False,
        expected_columns=_OFAC_SDN_COLUMNS,
    ),
    SourceDefinition(
        source_key="ofac_alt",
        display_name="OFAC Aliases (Tier 1, authoritative)",
        relative_path="sanctions/ofac_alt.csv",
        category=SourceCategory.SANCTIONS_LIST,
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        source_type=SourceType.OFAC_SDN,
        format=SourceFormat.CSV,
        known_record_count=20338,
        enabled=True,
        ingestion_strategy=IngestionStrategy.LOOKUP_ONLY,
        description=(
            "Aliases for ofac_sdn.csv entities. No header row. Consulted by "
            "Tier1OfacLookupProvider alongside ofac_sdn.csv, never bulk-loaded."
        ),
        has_header=False,
        expected_columns=_OFAC_ALT_COLUMNS,
    ),
    SourceDefinition(
        source_key="ofac_add",
        display_name="OFAC Addresses (Tier 1, authoritative)",
        relative_path="sanctions/ofac_add.csv",
        category=SourceCategory.SANCTIONS_LIST,
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        source_type=SourceType.OFAC_SDN,
        format=SourceFormat.CSV,
        known_record_count=24930,
        enabled=True,
        ingestion_strategy=IngestionStrategy.LOOKUP_ONLY,
        description=(
            "Addresses for ofac_sdn.csv entities. No header row. Registered but not yet "
            "consulted by any Phase 2 provider -- see docs/phase-2-ingestion.md limitations."
        ),
        has_header=False,
        expected_columns=_OFAC_ADD_COLUMNS,
    ),
    SourceDefinition(
        source_key="opensanctions",
        display_name="OpenSanctions Consolidated Targets (Tier 1, authoritative)",
        relative_path="sanctions/opensanctions_targets.csv",
        category=SourceCategory.WATCHLIST,
        source_tier=SourceTier.TIER_1_AUTHORITATIVE,
        source_type=SourceType.OPENSANCTIONS,
        format=SourceFormat.CSV,
        known_record_count=1_319_152,
        enabled=True,
        ingestion_strategy=IngestionStrategy.LOOKUP_ONLY,
        description=(
            "488 MB, 1.3M rows. Real global sanctions/watchlist/PEP aggregation. Phase 2 "
            "provides Tier1OpenSanctionsLookupProvider -- chunked streaming fuzzy search, "
            "never bulk-loaded."
        ),
    ),
    SourceDefinition(
        source_key="sample_ofac_sdn",
        display_name="Curated Sanctions Fixture -- OFAC-style (Tier 2, demo)",
        relative_path="sanctions/sample_ofac_sdn.csv",
        category=SourceCategory.SANCTIONS_LIST,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.CURATED_OFAC,
        format=SourceFormat.CSV,
        known_record_count=17,
        enabled=True,
        ingestion_strategy=IngestionStrategy.CURATED_FIXTURE,
        description=(
            "Deliberately curated 17-entity fixture, interlocked with the adverse-media "
            "articles and UBO showcase graph. NOT authoritative -- see docs/phase-0-dataset-"
            "audit.md SS4.5. One known malformed trailing row (documented)."
        ),
    ),
    SourceDefinition(
        source_key="sample_ofac_alt",
        display_name="Curated Sanctions Fixture Aliases (Tier 2, demo)",
        relative_path="sanctions/sample_ofac_alt.csv",
        category=SourceCategory.SANCTIONS_LIST,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.CURATED_OFAC,
        format=SourceFormat.CSV,
        known_record_count=15,
        enabled=True,
        ingestion_strategy=IngestionStrategy.CURATED_FIXTURE,
        description="Aliases for sample_ofac_sdn.csv entities.",
    ),
    SourceDefinition(
        source_key="sample_opensanctions",
        display_name="Curated OpenSanctions-style Fixture (Tier 2, demo)",
        relative_path="sanctions/sample_opensanctions.csv",
        category=SourceCategory.WATCHLIST,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.CURATED_OPENSANCTIONS,
        format=SourceFormat.CSV,
        known_record_count=21,
        enabled=True,
        ingestion_strategy=IngestionStrategy.CURATED_FIXTURE,
        description=(
            "Curated fixture including PEP entries. Contains one known malformed row "
            "(os-003401 / Sokolov, missing a delimiter) -- documented in docs/data-dictionary.md."
        ),
    ),
    SourceDefinition(
        source_key="article_clean",
        display_name="Adverse Media Fixture -- Clean Control",
        relative_path="articles/clean_article.txt",
        category=SourceCategory.ADVERSE_MEDIA,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.ADVERSE_MEDIA_FIXTURE,
        format=SourceFormat.TEXT,
        known_record_count=1,
        enabled=True,
        ingestion_strategy=IngestionStrategy.CURATED_FIXTURE,
        description="True-negative fixture article. No risk content, no sanctions match.",
    ),
    SourceDefinition(
        source_key="article_adverse_hit",
        display_name="Adverse Media Fixture -- True Positive",
        relative_path="articles/adverse_hit_article.txt",
        category=SourceCategory.ADVERSE_MEDIA,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.ADVERSE_MEDIA_FIXTURE,
        format=SourceFormat.TEXT,
        known_record_count=1,
        enabled=True,
        ingestion_strategy=IngestionStrategy.CURATED_FIXTURE,
        description="True-positive fixture article naming entities confirmed in the Tier-2 sanctions fixture.",
    ),
    SourceDefinition(
        source_key="article_adversarial",
        display_name="Adverse Media Fixture -- True Positive + Prompt Injection",
        relative_path="articles/adversarial_article.txt",
        category=SourceCategory.ADVERSE_MEDIA,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.ADVERSE_MEDIA_FIXTURE,
        format=SourceFormat.TEXT,
        known_record_count=1,
        enabled=True,
        ingestion_strategy=IngestionStrategy.CURATED_FIXTURE,
        description=(
            "True-positive fixture article that ALSO embeds a live prompt-injection payload "
            "targeting the risk score. The acceptance test for 'DATA IS DATA, NOT INSTRUCTIONS'."
        ),
    ),
    SourceDefinition(
        source_key="ubo_simple",
        display_name="UBO Graph Fixture -- Clean Control",
        relative_path="ubo/simple_structure.json",
        category=SourceCategory.OWNERSHIP_GRAPH,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.UBO_GRAPH_FIXTURE,
        format=SourceFormat.JSON,
        known_record_count=3,
        enabled=True,
        ingestion_strategy=IngestionStrategy.CURATED_FIXTURE,
        description="3 entities, 2 ownership edges. No entity resolves against any sanctions tier.",
    ),
    SourceDefinition(
        source_key="ubo_showcase",
        display_name="UBO Graph Fixture -- Hidden Sanctioned UBO",
        relative_path="ubo/showcase_structure.json",
        category=SourceCategory.OWNERSHIP_GRAPH,
        source_tier=SourceTier.TIER_2_CURATED_DEMO,
        source_type=SourceType.UBO_GRAPH_FIXTURE,
        format=SourceFormat.JSON,
        known_record_count=4,
        enabled=True,
        ingestion_strategy=IngestionStrategy.CURATED_FIXTURE,
        description="4 entities, 3 edges. A sanctioned individual sits 3 ownership hops deep (48% effective).",
    ),
)


class SourceRegistry:
    """Read-only accessor over DATASET_SOURCES, resolving relative paths
    against the configured raw_data_dir. Availability checks are a single
    cheap Path.exists() call -- never a file read."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._by_key = {s.source_key: s for s in DATASET_SOURCES}

    def list_sources(self) -> list[SourceDefinition]:
        return list(DATASET_SOURCES)

    def get_source(self, source_key: str) -> SourceDefinition | None:
        return self._by_key.get(source_key)

    def resolve_path(self, source: SourceDefinition) -> Path:
        return self._settings.raw_data_dir / source.relative_path

    def check_file_availability(self, source_key: str) -> bool:
        """Returns False (never raises) for an unknown key or a missing file."""
        source = self.get_source(source_key)
        if source is None:
            return False
        return self.resolve_path(source).is_file()
