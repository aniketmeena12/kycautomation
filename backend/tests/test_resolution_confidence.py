"""
Confidence engine + weight configuration.

The arithmetic is pinned down precisely here because it is the number a human
reviewer will eventually act on. If these change, that is a deliberate policy
change and should require editing a test.
"""

import json

import pytest

from app.core.enums import EntityMatchStatus
from app.resolution.confidence import ConfidenceEngine
from app.resolution.config import DEFAULT_WEIGHTS_PATH, ResolutionWeights, load_weights
from app.resolution.schemas import ScorerResult


def _r(scorer, score, applicable=True, conflict=False) -> ScorerResult:
    return ScorerResult(
        scorer=scorer, applicable=applicable, score=score, reason=f"{scorer} test", is_conflict=conflict
    )


# ------------------------------------------------------------------ config


def test_weights_load_from_file_not_code():
    weights = load_weights()
    assert weights.positive_weights["name"] > 0
    assert weights.thresholds.high_confidence > weights.thresholds.possible


def test_weights_config_rejects_missing_scorer():
    with pytest.raises(ValueError, match="missing positive_weights"):
        ResolutionWeights(
            positive_weights={"name": 40.0},  # everything else missing
            conflict_penalties={},
            thresholds={"name_floor": 0.7, "high_confidence": 85, "possible": 60, "rejected_below": 40},
            scorer_thresholds={
                "name_match": 0.85,
                "alias_match": 0.85,
                "organization_match": 0.85,
                "partial_dob_year_credit": 0.5,
            },
        )


def test_weights_config_rejects_negative_weight():
    full = {
        s: 1.0
        for s in (
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
    }
    full["name"] = -5.0
    with pytest.raises(ValueError, match="must be >= 0"):
        ResolutionWeights(
            positive_weights=full,
            conflict_penalties={},
            thresholds={"name_floor": 0.7, "high_confidence": 85, "possible": 60, "rejected_below": 40},
            scorer_thresholds={
                "name_match": 0.85,
                "alias_match": 0.85,
                "organization_match": 0.85,
                "partial_dob_year_credit": 0.5,
            },
        )


def test_weights_file_missing_raises_rather_than_defaulting_silently(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_weights(tmp_path / "nope.json")


def test_confidence_respects_custom_weights_from_config(tmp_path):
    """Proves weights are genuinely data-driven, not baked into the code: the
    SAME scorer inputs produce a different confidence under a different
    config file."""
    base = json.loads(DEFAULT_WEIGHTS_PATH.read_text(encoding="utf-8"))
    base.pop("_comment", None)

    scorer_inputs = [_r("name", 1.0), _r("dob", 0.0)]
    default_confidence = ConfidenceEngine(load_weights()).compute(scorer_inputs).final

    # Re-weight so dob is worth nothing and only the (perfect) name counts.
    base["positive_weights"]["dob"] = 0.0
    custom_path = tmp_path / "custom.json"
    custom_path.write_text(json.dumps(base), encoding="utf-8")

    custom_confidence = (
        ConfidenceEngine(load_weights(custom_path)).compute([_r("name", 1.0), _r("dob", 0.0)]).final
    )

    assert custom_confidence == 100.0
    assert custom_confidence != default_confidence  # the config file actually drove the change


# ------------------------------------------------------------- arithmetic


def test_not_applicable_scorers_shrink_the_denominator_not_the_score():
    """A pair lacking DOB data must not be punished for it."""
    engine = ConfidenceEngine()
    with_dob_absent = engine.compute([_r("name", 1.0), _r("dob", None, applicable=False)])
    assert with_dob_absent.final == 100.0
    assert "dob" in with_dob_absent.not_applicable


def test_conflict_both_forfeits_weight_and_subtracts_penalty():
    engine = ConfidenceEngine()
    breakdown = engine.compute([_r("name", 1.0), _r("entity_type", 0.0, conflict=True)])
    w = engine.weights
    expected_base = (
        (1.0 * w.positive_weight("name"))
        / (w.positive_weight("name") + w.positive_weight("entity_type"))
        * 100
    )
    assert breakdown.base == pytest.approx(expected_base, abs=0.1)
    assert breakdown.penalty == w.conflict_penalty("entity_type")
    assert breakdown.final == pytest.approx(max(0.0, expected_base - breakdown.penalty), abs=0.1)


def test_confidence_is_clamped_to_zero_hundred():
    engine = ConfidenceEngine()
    floor = engine.compute(
        [_r("name", 0.0), _r("dob", 0.0, conflict=True), _r("entity_type", 0.0, conflict=True)]
    )
    assert floor.final == 0.0

    ceiling = engine.compute([_r("name", 1.0), _r("alias", 1.0), _r("dob", 1.0)])
    assert ceiling.final <= 100.0


def test_confidence_is_deterministic():
    engine = ConfidenceEngine()
    results_a = [_r("name", 0.9), _r("country", 1.0)]
    results_b = [_r("name", 0.9), _r("country", 1.0)]
    assert engine.compute(results_a).final == engine.compute(results_b).final


# ---------------------------------------------------------------- statuses


def test_name_floor_rejects_regardless_of_other_attributes():
    """Two entities agreeing on country and type are not a match if the names
    don't match."""
    engine = ConfidenceEngine()
    low_name = engine.compute([_r("name", 0.1), _r("country", 1.0), _r("entity_type", 1.0)])
    assert low_name.name_floor_triggered
    assert engine.status_for(low_name) == EntityMatchStatus.AUTO_REJECTED


def test_status_bands_follow_configured_thresholds():
    engine = ConfidenceEngine()
    t = engine.weights.thresholds

    high = engine.compute([_r("name", 1.0), _r("alias", 1.0), _r("dob", 1.0), _r("country", 1.0)])
    assert high.final >= t.high_confidence
    assert engine.status_for(high) == EntityMatchStatus.HIGH_CONFIDENCE


def test_engine_never_produces_human_only_statuses():
    """Hard invariant: 'Do not mark anything confirmed' (Phase 3 brief SS9)."""
    engine = ConfidenceEngine()
    machine_reachable = set()
    for name_score in [0.0, 0.5, 0.7, 0.8, 0.9, 1.0]:
        for extra in (
            [],
            [_r("dob", 1.0)],
            [_r("dob", 0.0, conflict=True)],
            [_r("country", 1.0), _r("alias", 1.0)],
        ):
            breakdown = engine.compute([_r("name", name_score), *extra])
            machine_reachable.add(engine.status_for(breakdown))

    assert EntityMatchStatus.CONFIRMED not in machine_reachable
    assert EntityMatchStatus.HUMAN_REVIEWED not in machine_reachable
    assert machine_reachable <= {
        EntityMatchStatus.CANDIDATE,
        EntityMatchStatus.POSSIBLE,
        EntityMatchStatus.HIGH_CONFIDENCE,
        EntityMatchStatus.AUTO_REJECTED,
    }


# ----------------------------------------------------------- explainability


def test_explanation_separates_positive_negative_and_not_applicable():
    engine = ConfidenceEngine()
    results = [_r("name", 1.0), _r("entity_type", 0.0, conflict=True), _r("dob", None, applicable=False)]
    breakdown = engine.compute(results)
    status = engine.status_for(breakdown)
    explanation = engine.explain(breakdown, status, results)

    assert any("name" in f for f in explanation.positive_factors)
    assert any("entity_type" in f for f in explanation.negative_factors)
    assert any("dob" in f for f in explanation.not_applicable_factors)
    assert explanation.summary
    assert explanation.overall_confidence == pytest.approx(breakdown.final, abs=0.01)


def test_nothing_in_an_explanation_is_opaque():
    """Every applicable scorer must appear somewhere in the explanation."""
    engine = ConfidenceEngine()
    results = [_r("name", 0.9), _r("country", 1.0), _r("dob", 0.0, conflict=True)]
    breakdown = engine.compute(results)
    explanation = engine.explain(breakdown, engine.status_for(breakdown), results)
    all_text = " ".join(
        explanation.positive_factors + explanation.negative_factors + explanation.not_applicable_factors
    )
    for scorer in ("name", "country", "dob"):
        assert scorer in all_text
