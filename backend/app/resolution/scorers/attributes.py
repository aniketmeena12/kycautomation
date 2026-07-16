"""
Attribute scorers -- the corroboration layer, and the project's actual
false-positive defence.

docs/phase-0-dataset-audit.md SS6 established (before any code existed) that
fuzzy name matching against the real lists produces genuine false positives
in this data -- e.g. searching the UBO graph's "Mohammad Al-Rashid" surfaces
`AL-RASHID TRUST` (an organization, not a person) and `AL-RASHIDI, NAWAF
AHMAD ALWAN` (a different given name). Nothing about the *names* rules those
out. What rules them out is entity type, nationality, and DOB. These scorers
are that mechanism.

Every one of them distinguishes three states, and the distinction is the
whole point:

  - agreement          -> positive score
  - contradiction      -> `is_conflict=True` (confidence engine subtracts)
  - absent on a side   -> `applicable=False` (no score, no penalty)

Conflating the third with the second would reject almost every true match in
this dataset, because most Tier-1 OFAC rows have empty Remarks and therefore
no DOB at all (docs/data-dictionary.md). Absence of evidence is not evidence
of absence -- flagged as a requirement in Phase 0 SS6G.
"""

from __future__ import annotations

from app.resolution.config import get_weights
from app.resolution.normalization import (
    dob_year,
    normalize_country,
    normalize_dob,
    normalize_entity_type,
    normalize_identifier,
)
from app.resolution.schemas import ResolutionSubject, ScorerResult
from app.resolution.scorers.base import not_applicable
from app.resolution.scorers.name import compare_names


def _overlap(left: list[str], right: list[str], normalizer) -> tuple[set[str], set[str], set[str]]:
    lset = {n for n in (normalizer(v) for v in left) if n}
    rset = {n for n in (normalizer(v) for v in right) if n}
    return lset, rset, lset & rset


class _SetOverlapScorer:
    """Shared logic for country/nationality: both are 'two sets of codes;
    any overlap is agreement, no overlap when both sides are populated is a
    contradiction'."""

    name = "override-me"
    _subject_attr = "countries"
    _label = "Country"

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult:
        left = getattr(subject, self._subject_attr, []) or []
        right = getattr(candidate, self._subject_attr, []) or []
        lset, rset, shared = _overlap(left, right, normalize_country)

        if not lset or not rset:
            return not_applicable(self.name, f"{self._label} absent on at least one side.")

        if shared:
            return ScorerResult(
                scorer=self.name,
                applicable=True,
                score=1.0,
                reason=f"{self._label} matched: {sorted(shared)}",
            )

        return ScorerResult(
            scorer=self.name,
            applicable=True,
            score=0.0,
            is_conflict=True,
            reason=(f"{self._label} conflict: {sorted(lset)} vs {sorted(rset)} -- no overlap."),
        )


class CountryScorer(_SetOverlapScorer):
    name = "country"
    _subject_attr = "countries"
    _label = "Country"


class NationalityScorer(_SetOverlapScorer):
    name = "nationality"
    _subject_attr = "nationalities"
    _label = "Nationality"


class EntityTypeScorer:
    """Compatibility, not equality.

    An unmappable raw type (the two source vocabularies don't fully overlap --
    docs/data-dictionary.md) yields `applicable=False`, never a conflict. Only
    two *known, different* types are a contradiction -- and that is the check
    that kills the `AL-RASHID TRUST` (organization) vs. a sought person
    false positive.
    """

    name = "entity_type"

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult:
        left = normalize_entity_type(subject.entity_type)
        right = normalize_entity_type(candidate.entity_type)

        if left is None or right is None:
            return not_applicable(
                self.name,
                f"Entity type not comparable (subject={subject.entity_type!r}, candidate={candidate.entity_type!r}).",
            )
        if left == right:
            return ScorerResult(
                scorer=self.name, applicable=True, score=1.0, reason=f"Entity type matched: both '{left}'."
            )
        return ScorerResult(
            scorer=self.name,
            applicable=True,
            score=0.0,
            is_conflict=True,
            reason=f"Entity type conflict: '{left}' vs '{right}'.",
        )


class DobScorer:
    """Full-date agreement scores 1.0; year-only agreement scores a partial
    credit (configurable) because the sources genuinely differ in precision --
    the UBO fixtures carry `dob: "1975"` while OFAC Remarks carry
    '15 Mar 1975'. Scoring year-only agreement as a full match would overstate
    the evidence; scoring it as nothing would discard the strongest
    corroborating signal this dataset actually offers.

    A same-year/different-day pair is agreement-on-year, NOT a conflict --
    only different years are a contradiction. Two people born in the same year
    on different days is unremarkable; a source recording the wrong day is
    common. Different years is a real contradiction.
    """

    name = "dob"

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult:
        left = {d for d in (normalize_dob(v) for v in subject.dates_of_birth) if d}
        right = {d for d in (normalize_dob(v) for v in candidate.dates_of_birth) if d}

        if not left or not right:
            return not_applicable(self.name, "Date of birth absent on at least one side.")

        exact = left & right
        if exact:
            return ScorerResult(
                scorer=self.name,
                applicable=True,
                score=1.0,
                reason=f"Date of birth matched exactly: {sorted(exact)}.",
            )

        left_years = {dob_year(d) for d in left}
        right_years = {dob_year(d) for d in right}
        shared_years = {y for y in (left_years & right_years) if y}
        if shared_years:
            credit = get_weights().scorer_thresholds.partial_dob_year_credit
            return ScorerResult(
                scorer=self.name,
                applicable=True,
                score=credit,
                reason=(
                    f"Date of birth agrees on year {sorted(shared_years)} but not full date "
                    f"({sorted(left)} vs {sorted(right)}) -- partial credit."
                ),
            )

        return ScorerResult(
            scorer=self.name,
            applicable=True,
            score=0.0,
            is_conflict=True,
            reason=f"Date of birth conflict: {sorted(left)} vs {sorted(right)} -- different years.",
        )


class IdentifierScorer:
    """Registration/passport numbers.

    A shared identifier is the strongest positive signal available (hence the
    highest weight in config). But a *non*-shared identifier is only a weak
    conflict, and deliberately so: entities legitimately hold several
    identifiers of different kinds (a passport AND a commercial registry
    number), and this dataset does not label identifier types
    (docs/data-dictionary.md -- OpenSanctions `identifiers` is one free-text
    field). Treating 'these two lists don't intersect' as a strong
    contradiction would punish honest partial data.
    """

    name = "identifier"

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult:
        lset, rset, shared = _overlap(subject.identifiers, candidate.identifiers, normalize_identifier)

        if not lset or not rset:
            return not_applicable(self.name, "Identifiers absent on at least one side.")
        if shared:
            return ScorerResult(
                scorer=self.name, applicable=True, score=1.0, reason=f"Identifier matched: {sorted(shared)}."
            )
        return ScorerResult(
            scorer=self.name,
            applicable=True,
            score=0.0,
            is_conflict=True,
            reason=f"No shared identifier ({len(lset)} vs {len(rset)} recorded, none in common).",
        )


class OwnershipScorer:
    """Shared ownership/related-entity references.

    Operates on opaque refs supplied by the caller (e.g. UBO graph entity
    refs), so it stays generic: it never reads the ownership tables itself
    and cannot encode any particular graph's shape.
    """

    name = "ownership"

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult:
        lset = {r.strip().lower() for r in subject.related_entity_refs if r and r.strip()}
        rset = {r.strip().lower() for r in candidate.related_entity_refs if r and r.strip()}

        if not lset or not rset:
            return not_applicable(
                self.name, "Ownership/related-entity references absent on at least one side."
            )
        shared = lset & rset
        if shared:
            return ScorerResult(
                scorer=self.name,
                applicable=True,
                score=1.0,
                reason=f"Shared ownership/related entities: {sorted(shared)}.",
            )
        return ScorerResult(
            scorer=self.name,
            applicable=True,
            score=0.0,
            is_conflict=True,
            reason="No shared ownership/related entities despite both having them recorded.",
        )


class OrganizationScorer:
    """Employer / organizational association, fuzzy-compared as org names."""

    name = "organization"

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult:
        left = [o for o in subject.organizations if o]
        right = [o for o in candidate.organizations if o]
        if not left or not right:
            return not_applicable(self.name, "Organization association absent on at least one side.")

        best = 0.0
        best_pair: tuple[str, str] | None = None
        for a in left:
            for b in right:
                value, _ = compare_names(a, b, "organization")
                if value > best:
                    best, best_pair = value, (a, b)

        threshold = get_weights().scorer_thresholds.organization_match
        if best >= threshold:
            return ScorerResult(
                scorer=self.name,
                applicable=True,
                score=best,
                reason=f"Organization matched {best * 100:.0f}/100: '{best_pair[0]}' vs '{best_pair[1]}'.",
            )
        return ScorerResult(
            scorer=self.name,
            applicable=True,
            score=best,
            is_conflict=True,
            reason=(
                f"Organization mismatch (best {best * 100:.0f}/100 < {threshold * 100:.0f} threshold): "
                f"{left} vs {right}."
            ),
        )
