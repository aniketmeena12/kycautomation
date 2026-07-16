"""
Scorer contract.

Every scorer is an independently testable unit (Phase 3 brief SS2) that takes
two `ResolutionSubject`s and returns a `ScorerResult`. A scorer:

  - NEVER returns a bare boolean (brief SS3).
  - NEVER raises for missing data -- it returns `applicable=False` with a
    reason instead. A resolution run must not die because one entity lacks
    a DOB.
  - NEVER decides the outcome. It reports a similarity/conflict observation;
    `app/resolution/confidence.py` alone converts that into points using
    externally-configured weights. Keeping scoring and weighting separate is
    what lets weights be reconfigured without touching matching logic.
  - Contains no entity-specific logic. A scorer sees two subjects and
    compares their fields; it cannot know which dataset or which entity they
    came from.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.resolution.schemas import ResolutionSubject, ScorerResult


@runtime_checkable
class Scorer(Protocol):
    name: str

    def score(self, subject: ResolutionSubject, candidate: ResolutionSubject) -> ScorerResult: ...


def not_applicable(scorer: str, reason: str) -> ScorerResult:
    """Helper for the very common 'one side lacks this attribute' case.
    Deliberately distinct from a 0.0 score -- see ScorerResult.applicable."""
    return ScorerResult(scorer=scorer, applicable=False, score=None, reason=reason, confidence_impact=0.0)
