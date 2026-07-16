"""Entity-matching normalization -- pure functions, no I/O."""

from app.resolution.normalization import (
    dob_year,
    normalize_country,
    normalize_dob,
    normalize_entity_type,
    normalize_for_matching,
    normalize_identifier,
    normalize_person_name,
    strip_accents,
    strip_company_suffixes,
)


def test_normalize_for_matching_handles_unicode_punctuation_case_whitespace():
    assert normalize_for_matching("  AL-RASHID,   Mohammad  ") == "al rashid mohammad"
    assert normalize_for_matching("Müller & Söhne GmbH") == "muller sohne gmbh"
    assert normalize_for_matching(None) == ""
    assert normalize_for_matching("") == ""


def test_strip_accents_is_generic():
    assert strip_accents("Ali Rezá") == "Ali Reza"
    assert strip_accents("Zoë") == "Zoe"


def test_strip_company_suffixes():
    assert strip_company_suffixes("Greenfield Technologies Pte Ltd") == "greenfield technologies"
    assert strip_company_suffixes("ACME Global Holdings, Inc.") == "acme global"
    assert strip_company_suffixes("Northern Logistics GmbH") == "northern logistics"


def test_strip_company_suffixes_never_returns_empty_key():
    """A company literally named only of legal-form tokens must not normalize
    to '' -- an empty key would match everything."""
    assert strip_company_suffixes("Holdings Ltd") == "holdings ltd"
    assert strip_company_suffixes("Group PLC") == "group plc"


def test_normalize_person_name_is_word_order_independent():
    """The core cross-source fix: OFAC writes 'LAST, First'; OpenSanctions
    writes 'First Last'."""
    assert normalize_person_name("AL-RASHID, Mohammad") == normalize_person_name("Mohammad Al-Rashid")
    assert normalize_person_name("PETROV, Viktor Ivanovich") == normalize_person_name(
        "Viktor Ivanovich Petrov"
    )


def test_normalize_country_maps_uk_and_handles_spelled_out():
    assert normalize_country("UK") == "gb"
    assert normalize_country("gb") == "gb"
    assert normalize_country("AE") == "ae"
    assert normalize_country("United Kingdom") == "united kingdom"
    assert normalize_country(None) == ""


def test_normalize_identifier_ignores_formatting():
    assert normalize_identifier("HRB 145782") == normalize_identifier("HRB-145782") == "hrb145782"
    assert normalize_identifier("Passport A1234567 (UAE)") == "passporta1234567uae"


def test_normalize_entity_type_maps_both_vocabularies():
    # OFAC vocabulary
    assert normalize_entity_type("individual") == "person"
    assert normalize_entity_type("entity") == "organization"
    assert normalize_entity_type("vessel") == "vessel"
    # OpenSanctions vocabulary
    assert normalize_entity_type("Person") == "person"
    assert normalize_entity_type("Company") == "organization"
    assert normalize_entity_type("LegalEntity") == "organization"
    # Client vocabulary
    assert normalize_entity_type("Individual") == "person"
    assert normalize_entity_type("Corporate") == "organization"
    # Unknown -> None (scorer treats as not-applicable, never a conflict)
    assert normalize_entity_type("-0-") is None
    assert normalize_entity_type("something novel") is None
    assert normalize_entity_type(None) is None


def test_normalize_dob_handles_the_real_source_formats():
    assert normalize_dob("15 Mar 1975") == "1975-03-15"  # OFAC Remarks style
    assert normalize_dob("1975-03-15") == "1975-03-15"  # OpenSanctions style
    assert normalize_dob("1975") == "1975"  # UBO fixture style (year only)
    assert normalize_dob(None) is None
    assert normalize_dob("no date") is None


def test_dob_year_extraction():
    assert dob_year("15 Mar 1975") == "1975"
    assert dob_year("1975") == "1975"
    assert dob_year(None) is None
