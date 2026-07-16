"""
Deterministic risk engine + Risk Factor Registry.

The arithmetic is pinned precisely because it is the number a human reviewer
will act on. If these change, that's a deliberate policy change and should
require editing a test.
"""

import json
from datetime import datetime, timezone

import pytest

from app.core.enums import RiskBand
from app.risk.config import (
    DEFAULT_RISK_FACTORS_PATH,
    AlertConfig,
    RiskFactor,
    RiskRegistry,
    ScoringConfig,
    TriggerCondition,
    load_risk_registry,
)
from app.risk.engine import RiskEngine
from app.risk.schemas import RiskSignal

BANDS = {RiskBand.LOW: 0.0, RiskBand.MEDIUM: 25.0, RiskBand.HIGH: 50.0, RiskBand.CRITICAL: 80.0}


def signal(signal_type="S", confidence=1.0, key="k1", **kw) -> RiskSignal:
    return RiskSignal(
        signal_type=signal_type,
        confidence=confidence,
        source=kw.pop("source", "test"),
        summary=kw.pop("summary", "test signal"),
        dedup_key=key,
        occurred_at=datetime.now(timezone.utc),
        **kw,
    )


def factor(fid="f1", weight=50.0, signal_type="S", **kw) -> RiskFactor:
    return RiskFactor(
        id=fid,
        name=kw.pop("name", fid),
        description="d",
        category=kw.pop("category", "TEST"),
        severity=kw.pop("severity", RiskBand.HIGH),
        weight=weight,
        max_contribution=kw.pop("max_contribution", weight),
        trigger_condition=TriggerCondition(signal_type=signal_type, **kw.pop("condition", {})),
        **kw,
    )


def registry(factors, formula="weight_x_confidence", max_total=100.0) -> RiskRegistry:
    return RiskRegistry(
        scoring=ScoringConfig(
            contribution_formula=formula, max_total_score=max_total, scoring_logic_version="test-v1"
        ),
        bands=BANDS,
        alerts=AlertConfig(),
        factors=factors,
    )


# ------------------------------------------------------------------ config


def test_real_registry_loads_from_file_not_code():
    reg = load_risk_registry()
    assert reg.factors
    assert reg.scoring.scoring_logic_version
    assert all(f.weight >= 0 for f in reg.factors)


def test_registry_file_missing_raises_rather_than_defaulting(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_risk_registry(tmp_path / "nope.json")


def test_registry_rejects_duplicate_factor_ids():
    with pytest.raises(ValueError, match="Duplicate risk factor ids"):
        registry([factor(fid="dup"), factor(fid="dup", signal_type="T")])


def test_registry_rejects_empty_factor_list():
    with pytest.raises(ValueError, match="at least one factor"):
        registry([])


def test_registry_rejects_non_binding_cap():
    with pytest.raises(ValueError, match="would never bind"):
        registry([factor(weight=10.0, max_contribution=99.0)])


def test_registry_rejects_unordered_bands():
    with pytest.raises(ValueError, match="must ascend"):
        RiskRegistry(
            scoring=ScoringConfig(scoring_logic_version="x"),
            bands={RiskBand.LOW: 0.0, RiskBand.MEDIUM: 90.0, RiskBand.HIGH: 50.0, RiskBand.CRITICAL: 80.0},
            alerts=AlertConfig(),
            factors=[factor()],
        )


def test_new_factor_needs_no_code_change(tmp_path):
    """The registry's core promise: append JSON, get a working factor."""
    raw = json.loads(DEFAULT_RISK_FACTORS_PATH.read_text(encoding="utf-8"))
    raw.pop("_comment", None)
    raw["factors"].append(
        {
            "id": "brand_new_factor_never_seen_in_code",
            "name": "Invented factor",
            "description": "Added purely via config.",
            "category": "NOVEL",
            "severity": "HIGH",
            "weight": 33.0,
            "confidence_multiplier": 1.0,
            "max_contribution": 33.0,
            "requires_entity_resolution": False,
            "enabled": True,
            "event_type": "OTHER",
            "trigger_condition": {"signal_type": "A_BRAND_NEW_SIGNAL_TYPE"},
        }
    )
    path = tmp_path / "custom.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    engine = RiskEngine(load_risk_registry(path))
    result = engine.score(1, [signal(signal_type="A_BRAND_NEW_SIGNAL_TYPE", confidence=1.0)])

    assert result.score == 33.0
    assert result.contributions[0].factor_id == "brand_new_factor_never_seen_in_code"


# --------------------------------------------------------------- formula


def test_matches_the_brief_worked_example():
    """Phase 4 brief SS5: weight 50 x confidence 0.82 -> contribution 41."""
    engine = RiskEngine(registry([factor(weight=50.0)]))
    result = engine.score(1, [signal(confidence=0.82)])
    assert result.contributions[0].contribution == 41.0
    assert result.score == 41.0


def test_confidence_multiplier_is_applied():
    engine = RiskEngine(registry([factor(weight=50.0, confidence_multiplier=0.5, max_contribution=50.0)]))
    result = engine.score(1, [signal(confidence=1.0)])
    assert result.contributions[0].contribution == 25.0


def test_weight_only_formula_ignores_signal_confidence():
    """Formula itself is config-driven, not hardcoded."""
    engine = RiskEngine(registry([factor(weight=40.0)], formula="weight_only"))
    low = engine.score(1, [signal(confidence=0.1)])
    high = engine.score(1, [signal(confidence=1.0)])
    assert low.score == high.score == 40.0


def test_contribution_is_capped_at_max_contribution():
    engine = RiskEngine(registry([factor(weight=50.0, max_contribution=20.0)]))
    result = engine.score(1, [signal(confidence=1.0)])
    contribution = result.contributions[0]
    assert contribution.raw_contribution == 50.0
    assert contribution.contribution == 20.0
    assert contribution.capped is True


def test_total_score_capped_at_max_total():
    engine = RiskEngine(
        registry(
            [factor(fid="a", weight=80.0, signal_type="A"), factor(fid="b", weight=80.0, signal_type="B")]
        )
    )
    result = engine.score(1, [signal(signal_type="A", key="k1"), signal(signal_type="B", key="k2")])
    assert result.total_before_cap == 160.0
    assert result.score == 100.0
    assert result.capped_at_max is True


def test_highest_contribution_per_factor_wins():
    """Two adverse-media articles are one finding with corroboration, not
    double the risk. Repetition is an ALERT concern, not a score multiplier."""
    engine = RiskEngine(registry([factor(weight=50.0)]))
    result = engine.score(1, [signal(confidence=0.4, key="a"), signal(confidence=0.9, key="b")])
    assert len(result.contributions) == 1
    assert result.contributions[0].contribution == 45.0  # 50 * 0.9, not 50*0.4 + 50*0.9


def test_score_is_deterministic():
    engine = RiskEngine(registry([factor(weight=50.0)]))
    a = engine.score(1, [signal(confidence=0.63)])
    b = engine.score(1, [signal(confidence=0.63)])
    assert a.score == b.score
    assert [c.contribution for c in a.contributions] == [c.contribution for c in b.contributions]


def test_no_signals_scores_zero_and_says_so():
    engine = RiskEngine(registry([factor()]))
    result = engine.score(1, [])
    assert result.score == 0.0
    assert result.band == RiskBand.LOW
    assert "No risk factors matched" in result.explanation


def test_disabled_factor_never_contributes():
    engine = RiskEngine(registry([factor(weight=90.0, enabled=False)]))
    result = engine.score(1, [signal()])
    assert result.score == 0.0


def test_unmatched_signal_is_reported_not_silently_dropped():
    engine = RiskEngine(registry([factor(signal_type="KNOWN")]))
    result = engine.score(1, [signal(signal_type="UNKNOWN", key="orphan")])
    assert result.unmatched_signals == ["orphan"]


# ------------------------------------------------------- trigger matching


def test_min_confidence_gate():
    engine = RiskEngine(registry([factor(weight=50.0, condition={"min_confidence": 0.8})]))
    assert engine.score(1, [signal(confidence=0.5)]).score == 0.0
    assert engine.score(1, [signal(confidence=0.9)]).score == 45.0


def test_metadata_equals_gate():
    engine = RiskEngine(registry([factor(weight=20.0, condition={"metadata_equals": {"flag": "pep"}})]))
    assert engine.score(1, [signal(metadata={"flag": "sanctions"})]).score == 0.0
    assert engine.score(1, [signal(metadata={"flag": "pep"})]).score == 20.0


# ----------------------------------------------------------------- bands


def test_bands_come_from_config():
    reg = registry([factor(weight=100.0, max_contribution=100.0)])
    assert reg.band_for(0.0) == RiskBand.LOW
    assert reg.band_for(24.9) == RiskBand.LOW
    assert reg.band_for(25.0) == RiskBand.MEDIUM
    assert reg.band_for(49.9) == RiskBand.MEDIUM
    assert reg.band_for(50.0) == RiskBand.HIGH
    assert reg.band_for(79.9) == RiskBand.HIGH
    assert reg.band_for(80.0) == RiskBand.CRITICAL
    assert reg.band_for(100.0) == RiskBand.CRITICAL


def test_delta_and_previous_band_computed():
    engine = RiskEngine(registry([factor(weight=60.0)]))
    result = engine.score(1, [signal(confidence=1.0)], previous_score=20.0)
    assert result.previous_score == 20.0
    assert result.previous_band == RiskBand.LOW
    assert result.score == 60.0
    assert result.band == RiskBand.HIGH
    assert result.delta == 40.0


# -------------------------------------------------------- explainability


def test_every_contribution_shows_its_arithmetic():
    engine = RiskEngine(registry([factor(weight=50.0, name="Sanctions")]))
    contribution = engine.score(1, [signal(confidence=0.82)]).contributions[0]
    assert "50" in contribution.reason and "0.82" in contribution.reason and "41" in contribution.reason
    assert contribution.weight == 50.0
    assert contribution.signal_confidence == 0.82


def test_explanation_names_the_drivers():
    engine = RiskEngine(
        registry(
            [
                factor(fid="big", weight=60.0, name="Big Factor", signal_type="A"),
                factor(fid="small", weight=10.0, name="Small Factor", signal_type="B"),
            ]
        )
    )
    result = engine.score(1, [signal(signal_type="A", key="a"), signal(signal_type="B", key="b")])
    assert "Big Factor" in result.explanation
    assert result.contributions[0].factor_id == "big"  # ranked by contribution


def test_contributions_ordered_deterministically_on_ties():
    engine = RiskEngine(
        registry(
            [factor(fid="zzz", weight=10.0, signal_type="A"), factor(fid="aaa", weight=10.0, signal_type="B")]
        )
    )
    result = engine.score(1, [signal(signal_type="A", key="a"), signal(signal_type="B", key="b")])
    assert [c.factor_id for c in result.contributions] == ["aaa", "zzz"]


def test_scoring_logic_version_is_recorded():
    engine = RiskEngine(registry([factor()]))
    assert engine.score(1, [signal()]).scoring_logic_version == "test-v1"


def test_engine_module_imports_nothing_that_could_reach_an_llm_or_the_network():
    """Structural guard on the project's core principle: the component that
    produces the authoritative risk number must have no import path to a
    model, an HTTP client, or a database session. Checked against the
    module's real import list, not a substring scan of prose."""
    import ast
    import inspect

    import app.risk.engine as engine_module

    tree = ast.parse(inspect.getsource(engine_module))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {"openai", "anthropic", "httpx", "requests", "urllib", "socket", "sqlalchemy"}
    assert not (imported & forbidden), f"risk engine must stay pure; found: {sorted(imported & forbidden)}"
