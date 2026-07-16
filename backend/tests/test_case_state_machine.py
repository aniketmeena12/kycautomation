"""
Phase 6 -- the case state machine (brief SS5).

Pure functions, so these are pure tests. "Illegal transitions must fail" is the
brief's requirement and the reason this module has no I/O: a component deciding
whether a compliance transition is legal must be trivially testable.
"""

from __future__ import annotations

import pytest

from app.casework.state_machine import (
    IllegalActionError,
    IllegalTransitionError,
    available_actions,
    is_terminal,
    resolve_action,
    validate_transition,
)
from app.core.enums import CaseStatus, ReviewAction

ALL_STATES = list(CaseStatus)


# --------------------------------------------------------------------- #
# Transitions
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "current,target",
    [
        (CaseStatus.OPEN, CaseStatus.UNDER_REVIEW),
        (CaseStatus.OPEN, CaseStatus.ESCALATED),
        (CaseStatus.OPEN, CaseStatus.CLOSED),
        (CaseStatus.UNDER_REVIEW, CaseStatus.SAR_REVIEW),
        (CaseStatus.UNDER_REVIEW, CaseStatus.ESCALATED),
        (CaseStatus.ESCALATED, CaseStatus.SAR_REVIEW),
        (CaseStatus.ESCALATED, CaseStatus.UNDER_REVIEW),  # de-escalation is legitimate
        (CaseStatus.SAR_REVIEW, CaseStatus.UNDER_REVIEW),  # a rejected draft sends it back
        (CaseStatus.SAR_REVIEW, CaseStatus.CLOSED),
    ],
)
def test_legal_transitions_are_permitted(current, target):
    validate_transition(current, target)  # must not raise


@pytest.mark.parametrize(
    "current,target",
    [
        (CaseStatus.OPEN, CaseStatus.SAR_REVIEW),  # cannot draft a SAR before any review
        (CaseStatus.CLOSED, CaseStatus.OPEN),
        (CaseStatus.CLOSED, CaseStatus.UNDER_REVIEW),
        (CaseStatus.CLOSED, CaseStatus.ESCALATED),
        (CaseStatus.CLOSED, CaseStatus.SAR_REVIEW),
    ],
)
def test_illegal_transitions_raise(current, target):
    with pytest.raises(IllegalTransitionError):
        validate_transition(current, target)


def test_closed_is_terminal_and_says_why():
    """Reopening a closed case would overwrite the fact that it was closed --
    and that fact, with its reviewer and timestamp, is what an auditor came to
    see."""
    assert is_terminal(CaseStatus.CLOSED)
    assert available_actions(CaseStatus.CLOSED) == []

    with pytest.raises(IllegalTransitionError) as exc:
        validate_transition(CaseStatus.CLOSED, CaseStatus.OPEN)
    assert "terminal" in str(exc.value)
    assert "new case" in str(exc.value)  # tells the caller the honest alternative


def test_a_no_op_is_not_a_transition():
    for state in ALL_STATES:
        validate_transition(state, state)  # must not raise


def test_error_message_names_the_legal_options():
    """A rejected transition must be actionable, not just refused."""
    with pytest.raises(IllegalTransitionError) as exc:
        validate_transition(CaseStatus.OPEN, CaseStatus.SAR_REVIEW)
    assert "UNDER_REVIEW" in str(exc.value)


# --------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------- #


def test_close_case_reaches_closed_from_every_active_state():
    for state in (CaseStatus.OPEN, CaseStatus.UNDER_REVIEW, CaseStatus.ESCALATED, CaseStatus.SAR_REVIEW):
        assert resolve_action(state, ReviewAction.CLOSE_CASE) == CaseStatus.CLOSED


def test_escalate_reaches_escalated():
    assert resolve_action(CaseStatus.OPEN, ReviewAction.ESCALATE) == CaseStatus.ESCALATED
    assert resolve_action(CaseStatus.UNDER_REVIEW, ReviewAction.ESCALATE) == CaseStatus.ESCALATED


def test_sar_actions_require_the_sar_review_state():
    """Approving a draft that does not exist is meaningless -- the state IS the
    precondition."""
    for state in (CaseStatus.OPEN, CaseStatus.UNDER_REVIEW, CaseStatus.ESCALATED):
        for action in (ReviewAction.APPROVE_DRAFT_SAR, ReviewAction.REJECT_DRAFT_SAR):
            with pytest.raises(IllegalActionError):
                resolve_action(state, action)


def test_approving_a_draft_sar_does_not_close_the_case():
    """Approving means 'fit to file'. Filing is out of scope, and a case that
    closed itself on approval would assert an outcome nobody recorded."""
    assert resolve_action(CaseStatus.SAR_REVIEW, ReviewAction.APPROVE_DRAFT_SAR) == CaseStatus.SAR_REVIEW


def test_rejecting_a_draft_sar_returns_the_case_for_more_work():
    assert resolve_action(CaseStatus.SAR_REVIEW, ReviewAction.REJECT_DRAFT_SAR) == CaseStatus.UNDER_REVIEW


def test_no_action_is_permitted_on_a_closed_case():
    for action in ReviewAction:
        with pytest.raises(IllegalActionError):
            resolve_action(CaseStatus.CLOSED, action)


def test_match_adjudication_moves_the_case_under_review():
    for action in (ReviewAction.CONFIRM_MATCH, ReviewAction.REJECT_MATCH):
        assert resolve_action(CaseStatus.OPEN, action) == CaseStatus.UNDER_REVIEW


def test_acknowledge_records_a_decision_without_moving_the_case():
    """A decision that changes nothing is still a decision worth recording."""
    assert resolve_action(CaseStatus.UNDER_REVIEW, ReviewAction.ACKNOWLEDGE) == CaseStatus.UNDER_REVIEW


def test_available_actions_never_offers_something_resolve_would_reject():
    """The API returns available_actions so a caller never guesses. If the two
    disagreed, a UI would render a button the server refuses."""
    for state in ALL_STATES:
        for action in available_actions(state):
            resolve_action(state, action)  # must not raise


def test_every_action_has_a_rule():
    """A ReviewAction with no rule would be unreachable -- silently dead."""
    from app.casework.state_machine import _ACTION_RULES

    assert set(_ACTION_RULES) == set(ReviewAction)


def test_every_action_rule_lands_on_a_legal_transition():
    """The second gate in resolve_action is not redundant: this proves no rule
    can introduce a state move that _LEGAL_TRANSITIONS forbids."""
    from app.casework.state_machine import _ACTION_RULES

    for action, rule in _ACTION_RULES.items():
        for state in rule.allowed_from:
            target = rule.target_state or state
            validate_transition(state, target)  # must not raise


def test_target_type_is_declared_exactly_when_a_target_is_required():
    """`requires_target` and `target_type` answer "needs a record?" and "which
    record?" -- they must never drift apart. An action that demands a target_id
    but cannot say what it points at gives a caller no way to supply one; an
    action that names a target type it never uses invites a spurious question.
    """
    from app.casework.state_machine import _ACTION_RULES

    for action, rule in _ACTION_RULES.items():
        if rule.requires_target:
            assert rule.target_type, f"{action.value} requires a target but names no target_type"
        else:
            assert rule.target_type is None, f"{action.value} needs no target but declares {rule.target_type}"


def test_action_requirements_cover_every_available_action():
    """A caller reading `action_requirements` must find an entry for every action
    in `available_actions` -- otherwise it is back to guessing for the gaps,
    which is exactly the bug this field exists to remove (the Phase 7 UI
    hardcoded the target rules, missed APPROVE/REJECT, and rendered a form the
    server rejected).
    """
    from app.casework.state_machine import action_rule

    for state in CaseStatus:
        actions = available_actions(state)
        for action in actions:
            rule = action_rule(action)  # must not raise for any available action
            assert isinstance(rule.requires_target, bool)
            assert rule.description, f"{action.value} has no description to show a reviewer"


def test_legacy_approve_and_reject_require_a_target():
    """Regression pin. These two were the exact actions a hand-copied UI table
    missed: both adjudicate a specific SAR draft, so neither may be accepted
    without knowing WHICH draft was approved. An unattributed approval of an
    unidentified document is not a compliance decision.
    """
    from app.casework.state_machine import action_rule

    for action in (ReviewAction.APPROVE, ReviewAction.REJECT):
        rule = action_rule(action)
        assert rule.requires_target is True
        assert rule.target_type == "SARDraft"
