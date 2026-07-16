"""
Pipeline tests, including the false-positive regressions that motivated the
whole design.

The `AL-RASHID` cases below are REAL findings from
docs/phase-0-dataset-audit.md SS6, which predicted -- before any code existed --
that fuzzy name matching against the authoritative lists would surface
`AL-RASHID TRUST` and `AL-RASHIDI, NAWAF AHMAD ALWAN` as false positives, and
that entity type / nationality / DOB would be what rules them out. These tests
assert the engine actually delivers that. They use those names as *test data*
-- nothing in app/resolution/ knows they exist.
"""

from app.core.enums import EntityMatchStatus
from app.resolution.pipeline import EntityResolutionPipeline
from app.resolution.schemas import ResolutionSubject


def subj(ref, name, **kw) -> ResolutionSubject:
    return ResolutionSubject(subject_ref=ref, name=name, **kw)


PIPELINE = EntityResolutionPipeline()

# The subject: the sanctioned individual hidden 3 layers deep in the UBO
# showcase graph (nationality UAE, dob 1975).
UBO_PERSON = subj(
    "ownership:showcase_structure:UBO-IND-004",
    "Mohammad Al-Rashid",
    entity_type="individual",
    nationalities=["AE"],
    dates_of_birth=["1975"],
)


def test_true_positive_reaches_high_confidence():
    true_hit = subj(
        "sanctions:CURATED_OFAC:001923",
        "AL-RASHID, Mohammad",
        entity_type="individual",
        aliases=["M. RASHID", "Mohammed AL RASHID"],
        nationalities=["AE"],
        dates_of_birth=["15 Mar 1975"],
    )
    result = PIPELINE.resolve_pair(UBO_PERSON, true_hit)
    assert result.status == EntityMatchStatus.HIGH_CONFIDENCE
    assert result.confidence >= 85
    assert not result.conflicting_attributes
    assert "name" in result.matched_attributes


def test_false_positive_trust_rejected_on_entity_type():
    """A TRUST is not a person, however well the name matches."""
    fp = subj("sanctions:OFAC_SDN:x1", "AL-RASHID TRUST", entity_type="entity")
    result = PIPELINE.resolve_pair(UBO_PERSON, fp)
    assert result.status == EntityMatchStatus.AUTO_REJECTED
    assert "entity_type" in result.conflicting_attributes


def test_false_positive_different_person_rejected():
    fp = subj(
        "sanctions:OFAC_SDN:x2",
        "AL-RASHIDI, NAWAF AHMAD ALWAN",
        entity_type="individual",
        nationalities=["KW"],
    )
    result = PIPELINE.resolve_pair(UBO_PERSON, fp)
    assert result.status == EntityMatchStatus.AUTO_REJECTED


def test_dob_mismatch_sinks_an_otherwise_perfect_name_match():
    """Negative evidence must be able to defeat a 100/100 name match."""
    same_name_wrong_dob = subj(
        "sanctions:OFAC_SDN:x3",
        "Mohammad Al-Rashid",
        entity_type="individual",
        nationalities=["AE"],
        dates_of_birth=["1999-01-01"],
    )
    result = PIPELINE.resolve_pair(UBO_PERSON, same_name_wrong_dob)
    assert "dob" in result.conflicting_attributes
    assert result.status != EntityMatchStatus.HIGH_CONFIDENCE


def test_country_mismatch_reduces_confidence():
    a = subj("x:1", "Acme Global Ltd", entity_type="company", countries=["DE"])
    same_country = subj("y:1", "Acme Global", entity_type="company", countries=["DE"])
    diff_country = subj("y:2", "Acme Global", entity_type="company", countries=["RU"])

    assert (
        PIPELINE.resolve_pair(a, same_country).confidence > PIPELINE.resolve_pair(a, diff_country).confidence
    )
    assert "country" in PIPELINE.resolve_pair(a, diff_country).conflicting_attributes


def test_company_suffix_normalization_still_matches():
    a = subj("x:1", "Greenfield Technologies Pte Ltd", entity_type="company", countries=["SG"])
    b = subj("y:1", "Greenfield Technologies", entity_type="company", countries=["SG"])
    result = PIPELINE.resolve_pair(a, b)
    assert result.status == EntityMatchStatus.HIGH_CONFIDENCE


def test_completely_unrelated_entities_are_rejected():
    a = subj("x:1", "Nordvale Dairy Cooperative", entity_type="company", countries=["DK"])
    b = subj("y:1", "Golden Crescent Shipping Ltd", entity_type="company", countries=["AE"])
    result = PIPELINE.resolve_pair(a, b)
    assert result.status == EntityMatchStatus.AUTO_REJECTED


def test_pipeline_is_generic_over_arbitrary_unseen_entities():
    """Entities that appear nowhere in any dataset resolve normally."""
    a = subj("zz:1", "Qxzjklm Synthetic Industries GmbH", entity_type="company", countries=["DE"])
    b = subj("zz:2", "Qxzjklm Synthetic Industries", entity_type="company", countries=["DE"])
    result = PIPELINE.resolve_pair(a, b)
    assert result.confidence > 85
    assert result.explanation.summary


def test_exact_ref_match_is_recorded_but_still_explained():
    same = subj("sanctions:OFAC_SDN:36", "AEROCARIBBEAN AIRLINES", entity_type="entity")
    result = PIPELINE.resolve_pair(same, same)
    assert any(r.scorer == "exact_ref" for r in result.scorer_results)
    assert result.status == EntityMatchStatus.HIGH_CONFIDENCE


def test_every_result_explains_itself():
    result = PIPELINE.resolve_pair(UBO_PERSON, subj("y:1", "AL-RASHID TRUST", entity_type="entity"))
    assert result.explanation.summary
    assert result.explanation.overall_confidence == result.confidence
    assert result.explanation.status == result.status
    assert result.scorer_results  # the raw per-feature detail is always available


def test_results_are_ranked_by_confidence(db_session):
    """resolve() must return best-first."""
    from app.ingestion.commands import ingest_dataset

    ingest_dataset(db_session, "sample_ofac_sdn")
    run = PIPELINE.resolve(db_session, UBO_PERSON)
    confidences = [r.confidence for r in run.results]
    assert confidences == sorted(confidences, reverse=True)
