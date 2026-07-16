"""
Alert engine -- pure rules, no DB.

Central theme: alerts key on CHANGE, never on absolute state. A client
sitting at CRITICAL with nothing new must not re-alert every cycle.
"""

from datetime import datetime, timezone

from app.core.enums import AlertTrigger, RiskBand, RiskEventType
from app.models.risk import RiskEvent
from app.risk.alerts import AlertEngine, band_rank
from app.risk.config import AlertConfig, RiskFactor, RiskRegistry, ScoringConfig, TriggerCondition
from app.risk.schemas import RiskScoreResult

BANDS = {RiskBand.LOW: 0.0, RiskBand.MEDIUM: 25.0, RiskBand.HIGH: 50.0, RiskBand.CRITICAL: 80.0}


def _registry(**alert_kwargs) -> RiskRegistry:
    defaults = dict(
        escalation_bands=[RiskBand.HIGH, RiskBand.CRITICAL],
        min_score_delta=15.0,
        critical_event_types=[RiskEventType.HIGH_CONFIDENCE_MATCH],
        repeated_signal_threshold=2,
        repeated_signal_excluded_event_types=[RiskEventType.OTHER],
        alert_on_provider_failure=True,
    )
    defaults.update(alert_kwargs)
    return RiskRegistry(
        scoring=ScoringConfig(scoring_logic_version="t"),
        bands=BANDS,
        alerts=AlertConfig(**defaults),
        factors=[
            RiskFactor(
                id="f",
                name="f",
                description="d",
                category="c",
                severity=RiskBand.HIGH,
                weight=10.0,
                max_contribution=10.0,
                trigger_condition=TriggerCondition(signal_type="S"),
            )
        ],
    )


def risk(
    score=60.0, band=RiskBand.HIGH, previous_score=None, previous_band=None, delta=None
) -> RiskScoreResult:
    return RiskScoreResult(
        client_id=1,
        score=score,
        band=band,
        previous_score=previous_score,
        previous_band=previous_band,
        delta=delta,
        scoring_logic_version="t",
        computed_at=datetime.now(timezone.utc),
        explanation="e",
    )


def event(event_type=RiskEventType.ADVERSE_MEDIA_HIT, severity=RiskBand.HIGH, key="k", eid=1) -> RiskEvent:
    e = RiskEvent(
        client_id=1,
        event_type=event_type,
        severity=severity,
        confidence=1.0,
        dedup_key=key,
        source="test",
        summary="s",
    )
    e.id = eid
    return e


# ------------------------------------------------------- band escalation


def test_band_escalation_fires_on_upward_move():
    proposals = AlertEngine(_registry()).propose(
        risk(score=60.0, band=RiskBand.HIGH, previous_score=10.0, previous_band=RiskBand.LOW, delta=50.0), []
    )
    escalations = [p for p in proposals if p.trigger == AlertTrigger.BAND_ESCALATION]
    assert len(escalations) == 1
    assert escalations[0].severity == RiskBand.HIGH


def test_band_escalation_does_not_fire_when_band_unchanged():
    """The anti-fatigue rule: sitting at HIGH is not news."""
    proposals = AlertEngine(_registry()).propose(
        risk(score=60.0, band=RiskBand.HIGH, previous_score=58.0, previous_band=RiskBand.HIGH, delta=2.0), []
    )
    assert not [p for p in proposals if p.trigger == AlertTrigger.BAND_ESCALATION]


def test_band_escalation_does_not_fire_on_downward_move():
    proposals = AlertEngine(_registry()).propose(
        risk(
            score=60.0, band=RiskBand.HIGH, previous_score=90.0, previous_band=RiskBand.CRITICAL, delta=-30.0
        ),
        [],
    )
    assert not [p for p in proposals if p.trigger == AlertTrigger.BAND_ESCALATION]


def test_band_escalation_ignores_non_escalation_bands():
    proposals = AlertEngine(_registry()).propose(
        risk(score=30.0, band=RiskBand.MEDIUM, previous_score=0.0, previous_band=RiskBand.LOW, delta=30.0), []
    )
    assert not [p for p in proposals if p.trigger == AlertTrigger.BAND_ESCALATION]


def test_first_ever_assessment_at_high_alerts_and_says_no_prior():
    proposals = AlertEngine(_registry()).propose(risk(score=60.0, band=RiskBand.HIGH), [])
    escalation = next(p for p in proposals if p.trigger == AlertTrigger.BAND_ESCALATION)
    assert "no prior assessment" in escalation.reason


# ----------------------------------------------------------- score delta


def test_score_delta_fires_at_threshold():
    proposals = AlertEngine(_registry()).propose(
        risk(
            score=40.0, band=RiskBand.MEDIUM, previous_score=25.0, previous_band=RiskBand.MEDIUM, delta=15.0
        ),
        [],
    )
    assert [p for p in proposals if p.trigger == AlertTrigger.SCORE_DELTA]


def test_score_delta_silent_below_threshold():
    proposals = AlertEngine(_registry()).propose(
        risk(score=30.0, band=RiskBand.MEDIUM, previous_score=25.0, previous_band=RiskBand.MEDIUM, delta=5.0),
        [],
    )
    assert not [p for p in proposals if p.trigger == AlertTrigger.SCORE_DELTA]


def test_score_delta_silent_on_decrease():
    proposals = AlertEngine(_registry()).propose(
        risk(score=10.0, band=RiskBand.LOW, previous_score=60.0, previous_band=RiskBand.HIGH, delta=-50.0), []
    )
    assert not [p for p in proposals if p.trigger == AlertTrigger.SCORE_DELTA]


def test_score_delta_threshold_is_config_driven():
    proposals = AlertEngine(_registry(min_score_delta=100.0)).propose(
        risk(score=60.0, band=RiskBand.HIGH, previous_score=10.0, previous_band=RiskBand.LOW, delta=50.0), []
    )
    assert not [p for p in proposals if p.trigger == AlertTrigger.SCORE_DELTA]


# ------------------------------------------------------- critical events


def test_critical_event_alerts():
    proposals = AlertEngine(_registry()).propose(
        risk(score=10.0, band=RiskBand.LOW),
        [event(event_type=RiskEventType.HIGH_CONFIDENCE_MATCH, severity=RiskBand.CRITICAL)],
    )
    critical = [p for p in proposals if p.trigger == AlertTrigger.CRITICAL_EVENT]
    assert len(critical) == 1
    assert critical[0].severity == RiskBand.CRITICAL


def test_non_critical_event_does_not_alert_as_critical():
    proposals = AlertEngine(_registry()).propose(
        risk(score=10.0, band=RiskBand.LOW), [event(event_type=RiskEventType.HIGH_RISK_GEOGRAPHY)]
    )
    assert not [p for p in proposals if p.trigger == AlertTrigger.CRITICAL_EVENT]


# ------------------------------------------------------ repeated signals


def test_repeated_signal_fires_at_threshold():
    events = [event(key="a", eid=1), event(key="b", eid=2)]
    proposals = AlertEngine(_registry()).propose(risk(score=10.0, band=RiskBand.LOW), events)
    repeated = [p for p in proposals if p.trigger == AlertTrigger.REPEATED_SIGNAL]
    assert len(repeated) == 1
    assert set(repeated[0].risk_event_ids) == {1, 2}


def test_single_signal_is_not_repetition():
    proposals = AlertEngine(_registry()).propose(risk(score=10.0, band=RiskBand.LOW), [event(key="a")])
    assert not [p for p in proposals if p.trigger == AlertTrigger.REPEATED_SIGNAL]


def test_excluded_event_types_never_count_as_repetition():
    """Regression for a real false alert found in live testing: two unrelated
    factors both mapping to the OTHER catch-all were reported as '2 new OTHER
    findings' -- repetition of a bucket, not of a finding."""
    events = [
        event(event_type=RiskEventType.OTHER, key="sector", eid=1),
        event(event_type=RiskEventType.OTHER, key="opacity", eid=2),
    ]
    proposals = AlertEngine(_registry()).propose(risk(score=10.0, band=RiskBand.LOW), events)
    assert not [p for p in proposals if p.trigger == AlertTrigger.REPEATED_SIGNAL]


def test_repeated_signal_exclusions_are_config_driven():
    """Removing OTHER from the exclusion list makes it count again -- proving
    the fix is policy, not a hardcoded special case."""
    events = [
        event(event_type=RiskEventType.OTHER, key="a", eid=1),
        event(event_type=RiskEventType.OTHER, key="b", eid=2),
    ]
    engine = AlertEngine(_registry(repeated_signal_excluded_event_types=[]))
    assert [
        p
        for p in engine.propose(risk(score=10.0, band=RiskBand.LOW), events)
        if p.trigger == AlertTrigger.REPEATED_SIGNAL
    ]


# ---------------------------------------------------- provider degraded


def test_provider_failure_alerts_at_low_severity():
    """Incomplete coverage is an operational problem, never evidence against
    the client."""
    proposals = AlertEngine(_registry()).propose(
        risk(score=10.0, band=RiskBand.LOW),
        [event(event_type=RiskEventType.PROVIDER_FAILURE, severity=RiskBand.LOW)],
    )
    degraded = [p for p in proposals if p.trigger == AlertTrigger.PROVIDER_DEGRADED]
    assert len(degraded) == 1
    assert degraded[0].severity == RiskBand.LOW


def test_provider_failure_alert_can_be_disabled_by_config():
    proposals = AlertEngine(_registry(alert_on_provider_failure=False)).propose(
        risk(score=10.0, band=RiskBand.LOW), [event(event_type=RiskEventType.PROVIDER_FAILURE)]
    )
    assert not [p for p in proposals if p.trigger == AlertTrigger.PROVIDER_DEGRADED]


# ---------------------------------------------------------------- misc


def test_quiet_cycle_proposes_nothing():
    proposals = AlertEngine(_registry()).propose(
        risk(score=10.0, band=RiskBand.LOW, previous_score=10.0, previous_band=RiskBand.LOW, delta=0.0), []
    )
    assert proposals == []


def test_dedup_keys_are_stable_across_identical_cycles():
    engine = AlertEngine(_registry())
    r = risk(score=60.0, band=RiskBand.HIGH, previous_score=10.0, previous_band=RiskBand.LOW, delta=50.0)
    first = {p.dedup_key for p in engine.propose(r, [])}
    second = {p.dedup_key for p in engine.propose(r, [])}
    assert first == second


def test_band_rank_ordering():
    assert (
        band_rank(RiskBand.LOW)
        < band_rank(RiskBand.MEDIUM)
        < band_rank(RiskBand.HIGH)
        < band_rank(RiskBand.CRITICAL)
    )
    assert band_rank(None) == -1
