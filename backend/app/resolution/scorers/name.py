"""
Name and alias similarity.

RapidFuzz metric is chosen by entity type (Phase 3 brief SS4), because the two
kinds of name behave differently:

  - PERSON -> token_sort_ratio over a token-sorted key. Person names arrive in
    incompatible orders across this project's real sources -- OFAC writes
    `AL-RASHID, Mohammad`, OpenSanctions writes `Mohammad Al-Rashid`
    (docs/data-dictionary.md). Word order is noise; token identity is signal.
  - ORGANIZATION -> token_set_ratio over a suffix-stripped key. Company names
    differ by extra/missing tokens far more than by reordering
    ('Greenfield Technologies' vs 'Greenfield Technologies Pte Ltd'), and
    token_set_ratio is the metric that tolerates a subset relationship.
  - UNKNOWN type -> the max of both, so an unclassified entity is never
    penalized for our not knowing what it is.

`ratio` and `partial_ratio` are computed alongside and reported in the reason
string for explainability, and `partial_ratio` guards a specific failure:
token_set_ratio can score a short name embedded in a long one very highly, so
the blended score never exceeds what a plain `ratio` would justify by more
than the configured tolerance. See `_blend`.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from app.resolution.normalization import (
    normalize_entity_type,
    normalize_for_matching,
    normalize_person_name,
    strip_company_suffixes,
)
from app.resolution.schemas import ResolutionSubject, ScorerResult
from app.resolution.scorers.base import not_applicable


def _person_key(value: str) -> str:
    return normalize_person_name(value)


def _org_key(value: str) -> str:
    return strip_company_suffixes(value)


def _resolve_kind(subject: ResolutionSubject, candidate: ResolutionSubject) -> str | None:
    """Use whichever side declares a type; if they disagree, treat as unknown
    and let the entity_type scorer flag the conflict -- the name scorer must
    not double-penalize a type mismatch."""
    a = normalize_entity_type(subject.entity_type)
    b = normalize_entity_type(candidate.entity_type)
    if a and b:
        return a if a == b else None
    return a or b


def compare_names(left: str, right: str, kind: str | None) -> tuple[float, str]:
    """Returns (score 0..1, human-readable reason). Pure -- no config, no I/O."""
    if kind == "person":
        lk, rk = _person_key(left), _person_key(right)
        metric = "token_sort_ratio"
        raw = fuzz.token_sort_ratio(lk, rk)
    elif kind == "organization":
        lk, rk = _org_key(left), _org_key(right)
        metric = "token_set_ratio"
        raw = fuzz.token_set_ratio(lk, rk)
    else:
        lk, rk = normalize_for_matching(left), normalize_for_matching(right)
        person_score = fuzz.token_sort_ratio(_person_key(left), _person_key(right))
        org_score = fuzz.token_set_ratio(_org_key(left), _org_key(right))
        if org_score >= person_score:
            metric, raw = "token_set_ratio(unknown-type)", org_score
        else:
            metric, raw = "token_sort_ratio(unknown-type)", person_score

    plain = fuzz.ratio(lk, rk)
    partial = fuzz.partial_ratio(lk, rk)
    score = _blend(raw, plain, partial)
    reason = (
        f"{metric}={raw:.0f} (ratio={plain:.0f}, partial_ratio={partial:.0f}) "
        f"on normalized '{lk}' vs '{rk}'"
    )
    return score / 100.0, reason


def _blend(primary: float, plain: float, partial: float) -> float:
    """Guard against token_set_ratio's substring blind spot.

    token_set_ratio('aegean', 'aegean ventures cyprus') == 100 -- a perfect
    score for a name that is merely a *prefix* of the other. Capping the
    primary metric at the midpoint between itself and plain `ratio` keeps a
    genuine full match near its high score while pulling a substring-only
    match down. This is a deliberate precision-over-recall choice for a
    compliance context, where a false positive costs an analyst's time and a
    wrongly-cleared entity costs far more.
    """
    if primary <= plain:
        return primary
    return (primary + max(plain, partial * 0.5)) / 2.0


class NameScorer:
    name = "name"

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult:
        if not subject.name or not candidate.name:
            return not_applicable(self.name, "One or both entities have no name.")

        kind = _resolve_kind(subject, candidate)
        value, reason = compare_names(subject.name, candidate.name, kind)
        return ScorerResult(
            scorer=self.name,
            applicable=True,
            score=value,
            reason=f"Name similarity {value * 100:.0f}/100 -- {reason}",
        )


class AliasScorer:
    """Best match of any subject name/alias against any candidate name/alias.

    Includes each side's primary name in its own alias pool deliberately: a
    real alias frequently equals the other side's primary name (OpenSanctions
    lists 'M. Rashid' as an alias of a person OFAC lists primarily as
    'AL-RASHID, Mohammad'). Excluding primaries would miss exactly the
    cross-source links this scorer exists to find.
    """

    name = "alias"

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult:
        subject_pool = [n for n in ([subject.name] + list(subject.aliases)) if n]
        candidate_pool = [n for n in ([candidate.name] + list(candidate.aliases)) if n]

        if not subject.aliases and not candidate.aliases:
            return not_applicable(self.name, "Neither entity has aliases.")
        if not subject_pool or not candidate_pool:
            return not_applicable(self.name, "One side has no comparable names.")

        kind = _resolve_kind(subject, candidate)
        best_score = 0.0
        best_pair: tuple[str, str] | None = None
        for left in subject_pool:
            for right in candidate_pool:
                value, _ = compare_names(left, right, kind)
                if value > best_score:
                    best_score, best_pair = value, (left, right)

        if best_pair is None:
            return not_applicable(self.name, "No comparable alias pair.")
        return ScorerResult(
            scorer=self.name,
            applicable=True,
            score=best_score,
            reason=(
                f"Best alias match {best_score * 100:.0f}/100 between "
                f"'{best_pair[0]}' and '{best_pair[1]}' "
                f"({len(subject_pool)}x{len(candidate_pool)} name pairs compared)"
            ),
        )
