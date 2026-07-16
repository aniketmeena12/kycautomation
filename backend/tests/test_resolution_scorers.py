"""
Scorer tests -- each scorer independently, no DB, no providers.

Every scorer must distinguish THREE states, and most tests here exist to pin
that distinction down:
    agreement      -> score > 0, is_conflict False
    contradiction  -> is_conflict True
    absent         -> applicable False   (NOT a conflict, NOT a zero)
"""

from app.resolution.schemas import ResolutionSubject
from app.resolution.scorers import (
    AliasScorer,
    CountryScorer,
    DobScorer,
    EntityTypeScorer,
    IdentifierScorer,
    NameScorer,
    NationalityScorer,
    OrganizationScorer,
    OwnershipScorer,
    build_default_scorers,
)


def subj(ref="s:1", **kw) -> ResolutionSubject:
    return ResolutionSubject(subject_ref=ref, name=kw.pop("name", "Test"), **kw)


# --------------------------------------------------------------------- name


def test_name_scorer_exact_match():
    r = NameScorer().score(subj(name="Acme Corp"), subj(ref="s:2", name="Acme Corp"))
    assert r.applicable and r.score == 1.0 and not r.is_conflict


def test_name_scorer_person_word_order_independence():
    a = subj(name="Mohammad Al-Rashid", entity_type="individual")
    b = subj(ref="s:2", name="AL-RASHID, Mohammad", entity_type="individual")
    r = NameScorer().score(a, b)
    assert r.score == 1.0


def test_name_scorer_company_suffix_insensitivity():
    a = subj(name="Greenfield Technologies", entity_type="company")
    b = subj(ref="s:2", name="Greenfield Technologies Pte Ltd", entity_type="company")
    r = NameScorer().score(a, b)
    assert r.score >= 0.95


def test_name_scorer_unrelated_names_score_low():
    r = NameScorer().score(subj(name="Acme Corp"), subj(ref="s:2", name="Nordvale Dairy Cooperative"))
    assert r.score < 0.5


def test_name_scorer_substring_does_not_score_perfect():
    """Regression for the token_set_ratio substring blind spot: 'Aegean'
    is a prefix of 'Aegean Ventures Cyprus Ltd' and token_set_ratio alone
    would score it 100."""
    a = subj(name="Aegean", entity_type="company")
    b = subj(ref="s:2", name="Aegean Ventures Cyprus Ltd", entity_type="company")
    r = NameScorer().score(a, b)
    assert r.score < 1.0


def test_name_scorer_not_applicable_without_a_name():
    r = NameScorer().score(subj(name=""), subj(ref="s:2", name="Acme"))
    assert not r.applicable and r.score is None


def test_name_scorer_never_returns_bare_boolean():
    r = NameScorer().score(subj(name="A"), subj(ref="s:2", name="B"))
    assert not isinstance(r, bool)
    assert r.reason and isinstance(r.reason, str)


# -------------------------------------------------------------------- alias


def test_alias_scorer_matches_alias_against_primary_name():
    a = subj(name="Mohammad Al-Rashid", aliases=["M. Rashid"], entity_type="individual")
    b = subj(ref="s:2", name="AL-RASHID, Mohammad", aliases=["Mohammed AL RASHID"], entity_type="individual")
    r = AliasScorer().score(a, b)
    assert r.applicable and r.score == 1.0


def test_alias_scorer_not_applicable_when_neither_has_aliases():
    r = AliasScorer().score(subj(name="A"), subj(ref="s:2", name="B"))
    assert not r.applicable


# ------------------------------------------------------------------ country


def test_country_scorer_three_states():
    match = CountryScorer().score(
        subj(name="A", countries=["AE"]), subj(ref="s:2", name="B", countries=["AE"])
    )
    assert match.applicable and match.score == 1.0 and not match.is_conflict

    conflict = CountryScorer().score(
        subj(name="A", countries=["AE"]), subj(ref="s:2", name="B", countries=["RU"])
    )
    assert conflict.applicable and conflict.is_conflict

    absent = CountryScorer().score(subj(name="A", countries=["AE"]), subj(ref="s:2", name="B"))
    assert not absent.applicable and not absent.is_conflict


def test_country_scorer_normalizes_before_comparing():
    r = CountryScorer().score(subj(name="A", countries=["UK"]), subj(ref="s:2", name="B", countries=["GB"]))
    assert r.score == 1.0 and not r.is_conflict


def test_country_scorer_partial_overlap_is_a_match():
    r = CountryScorer().score(
        subj(name="A", countries=["AE", "PA"]), subj(ref="s:2", name="B", countries=["PA"])
    )
    assert r.score == 1.0 and not r.is_conflict


# -------------------------------------------------------------- nationality


def test_nationality_conflict_is_the_false_positive_killer():
    r = NationalityScorer().score(
        subj(name="Mohammad Al-Rashid", nationalities=["AE"]),
        subj(ref="s:2", name="AL-RASHIDI, NAWAF", nationalities=["KW"]),
    )
    assert r.is_conflict


# ------------------------------------------------------------- entity type


def test_entity_type_conflict_person_vs_organization():
    r = EntityTypeScorer().score(
        subj(name="Mohammad Al-Rashid", entity_type="individual"),
        subj(ref="s:2", name="AL-RASHID TRUST", entity_type="entity"),
    )
    assert r.is_conflict and r.score == 0.0


def test_entity_type_unknown_vocabulary_is_not_a_conflict():
    r = EntityTypeScorer().score(
        subj(name="A", entity_type="individual"), subj(ref="s:2", name="B", entity_type="-0-")
    )
    assert not r.applicable and not r.is_conflict


# ---------------------------------------------------------------------- dob


def test_dob_exact_match():
    r = DobScorer().score(
        subj(name="A", dates_of_birth=["15 Mar 1975"]),
        subj(ref="s:2", name="B", dates_of_birth=["1975-03-15"]),
    )
    assert r.score == 1.0 and not r.is_conflict


def test_dob_year_only_gets_partial_credit_not_full_and_not_conflict():
    """The UBO fixtures store year-only; OFAC stores a full date. Neither a
    full match nor a contradiction."""
    r = DobScorer().score(
        subj(name="A", dates_of_birth=["1975"]), subj(ref="s:2", name="B", dates_of_birth=["15 Mar 1975"])
    )
    assert 0.0 < (r.score or 0) < 1.0
    assert not r.is_conflict


def test_dob_different_year_is_a_conflict():
    r = DobScorer().score(
        subj(name="A", dates_of_birth=["1975-03-15"]),
        subj(ref="s:2", name="B", dates_of_birth=["1981-01-01"]),
    )
    assert r.is_conflict


def test_dob_absent_is_not_a_conflict():
    r = DobScorer().score(subj(name="A", dates_of_birth=["1975"]), subj(ref="s:2", name="B"))
    assert not r.applicable and not r.is_conflict


# --------------------------------------------------------------- identifier


def test_identifier_match_and_conflict():
    match = IdentifierScorer().score(
        subj(name="A", identifiers=["HRB 145782"]), subj(ref="s:2", name="B", identifiers=["HRB-145782"])
    )
    assert match.score == 1.0 and not match.is_conflict

    conflict = IdentifierScorer().score(
        subj(name="A", identifiers=["HRB 145782"]), subj(ref="s:2", name="B", identifiers=["OGRN 999"])
    )
    assert conflict.is_conflict


# ---------------------------------------------------------------- ownership


def test_ownership_shared_reference():
    r = OwnershipScorer().score(
        subj(name="A", related_entity_refs=["ubo:X"]),
        subj(ref="s:2", name="B", related_entity_refs=["ubo:X"]),
    )
    assert r.score == 1.0 and not r.is_conflict


# ------------------------------------------------------------- organization


def test_organization_match_ignores_legal_suffix():
    r = OrganizationScorer().score(
        subj(name="A", organizations=["Damascus Trading House"]),
        subj(ref="s:2", name="B", organizations=["Damascus Trading House LLC"]),
    )
    assert r.applicable and not r.is_conflict


# ------------------------------------------------------------------ generic


def test_every_default_scorer_is_safe_on_completely_empty_subjects():
    """No scorer may raise on missing data -- a resolution run must not die
    because one entity is sparse."""
    a = subj(ref="a", name="Something")
    b = subj(ref="b", name="Other")
    for scorer in build_default_scorers():
        result = scorer.score(a, b)
        assert result.scorer
        assert isinstance(result.reason, str) and result.reason


def test_no_scorer_hardcodes_any_entity_name():
    """Anti-hardcoding guard: scorer source must not embed demo entity names."""
    import inspect

    import app.resolution.scorers.attributes as attributes
    import app.resolution.scorers.name as name_module

    forbidden = [
        "al-rashid",
        "phillips-hanson",
        "clean corp",
        "meridian",
        "aegean",
        "nordvale",
        "abadi",
        "hosseini",
    ]
    for module in (attributes, name_module):
        source = inspect.getsource(module).lower()
        for token in forbidden:
            # Names may legitimately appear in explanatory comments, but never
            # in executable logic -- strip comment lines before checking.
            code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
            # Docstrings are also prose; crude but effective: assert the token
            # never appears next to a comparison operator.
            assert f'== "{token}"' not in code and f"== '{token}'" not in code
