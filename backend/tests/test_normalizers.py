"""Normalization utilities -- pure functions, no I/O."""

from datetime import date

from app.core.enums import SourceTier, SourceType
from app.ingestion.normalizers import (
    build_provenance,
    extract_dob_from_remarks,
    normalize_bool_flag,
    normalize_country_code,
    normalize_currency_code,
    normalize_datetime,
    normalize_entity_type,
    normalize_name,
    normalize_percentage,
    normalize_transaction_direction,
)


def test_normalize_country_code():
    assert normalize_country_code("uk") == "GB"
    assert normalize_country_code(" ae ") == "AE"
    assert normalize_country_code(None) is None
    assert normalize_country_code("-0-") is None
    assert normalize_country_code("ZZ") == "ZZ"  # unrecognized: passed through, never dropped


def test_normalize_currency_code():
    assert normalize_currency_code("UK pounds") == "GBP"
    assert normalize_currency_code("XYZ") == "XYZ"
    assert normalize_currency_code(None) is None


def test_normalize_name_collapses_whitespace_only():
    assert normalize_name("  AL-RASHID,   Mohammad  ") == "AL-RASHID, Mohammad"
    assert normalize_name("-0-") is None
    assert normalize_name(None) is None


def test_normalize_entity_type():
    assert normalize_entity_type(" individual ") == "individual"
    assert normalize_entity_type("-0-") is None


def test_normalize_transaction_direction():
    assert normalize_transaction_direction("123", "456", "123") == "OUTBOUND"
    assert normalize_transaction_direction("123", "456", "456") == "INBOUND"
    assert normalize_transaction_direction("123", "456", "999") == "UNKNOWN"
    assert normalize_transaction_direction("123", "456", None) == "UNKNOWN"


def test_normalize_percentage_clamps_and_handles_bad_input():
    assert normalize_percentage("80.0") == 80.0
    assert normalize_percentage(150) == 100.0
    assert normalize_percentage(-10) == 0.0
    assert normalize_percentage(None) is None
    assert normalize_percentage("not-a-number") is None


def test_normalize_datetime_multiple_formats():
    assert normalize_datetime("2025-08-22 07:16:38").isoformat().startswith("2025-08-22T07:16:38")
    assert normalize_datetime("2025-08-22").date().isoformat() == "2025-08-22"
    assert normalize_datetime(None) is None
    assert normalize_datetime("garbage") is None


def test_normalize_bool_flag():
    assert normalize_bool_flag(1) is True
    assert normalize_bool_flag(0) is False
    assert normalize_bool_flag("1") is True
    assert normalize_bool_flag("yes") is True
    assert normalize_bool_flag(None) is False


def test_extract_dob_from_remarks_generic_regex():
    """Runs identically for any remarks string -- no entity-specific logic."""
    assert extract_dob_from_remarks("DOB 15 Mar 1975; nationality UAE") == date(1975, 3, 15)
    assert extract_dob_from_remarks("DOB 25 Jul 1977; nationality Syria") == date(1977, 7, 25)
    assert extract_dob_from_remarks(None) is None
    assert extract_dob_from_remarks("no date here") is None


def test_build_provenance_shape():
    prov = build_provenance(
        source_dataset="kyc_profiles/clients_with_fatf_ofac.csv",
        source_tier=SourceTier.INTERNAL,
        source_type=SourceType.INTERNAL_KYC,
    )
    assert prov["source_dataset"] == "kyc_profiles/clients_with_fatf_ofac.csv"
    assert prov["source_tier"] == SourceTier.INTERNAL
    assert prov["ingested_at"] is not None
