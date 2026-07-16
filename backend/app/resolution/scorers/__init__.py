"""Scorer registry.

`build_default_scorers()` is the ordered set the pipeline runs. Adding a
feature is one entry here plus one class -- the confidence engine picks up
its weight from config by scorer name, and the explainability layer needs no
change.
"""

from app.resolution.scorers.attributes import (
    CountryScorer,
    DobScorer,
    EntityTypeScorer,
    IdentifierScorer,
    NationalityScorer,
    OrganizationScorer,
    OwnershipScorer,
)
from app.resolution.scorers.base import Scorer, not_applicable
from app.resolution.scorers.name import AliasScorer, NameScorer, compare_names

DEFAULT_SCORER_NAMES = (
    "name",
    "alias",
    "country",
    "nationality",
    "entity_type",
    "dob",
    "identifier",
    "ownership",
    "organization",
)


def build_default_scorers() -> list[Scorer]:
    return [
        NameScorer(),
        AliasScorer(),
        CountryScorer(),
        NationalityScorer(),
        EntityTypeScorer(),
        DobScorer(),
        IdentifierScorer(),
        OwnershipScorer(),
        OrganizationScorer(),
    ]


__all__ = [
    "AliasScorer",
    "CountryScorer",
    "DobScorer",
    "EntityTypeScorer",
    "IdentifierScorer",
    "NameScorer",
    "NationalityScorer",
    "OrganizationScorer",
    "OwnershipScorer",
    "Scorer",
    "build_default_scorers",
    "compare_names",
    "not_applicable",
    "DEFAULT_SCORER_NAMES",
]
