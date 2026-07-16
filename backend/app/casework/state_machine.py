"""
The case state machine (Phase 6 brief SS5).

Pure functions over enums. No database, no service, no I/O -- the same purity
discipline as app/risk/engine.py, and for the same reason: the component that
decides whether a compliance transition is legal must be trivially testable and
incapable of side effects.

TWO TABLES, NOT ONE
-------------------
`_LEGAL_TRANSITIONS` says which state moves are permissible at all.
`_ACTION_RULES` says which reviewer action may be taken from which state, and
where it lands.

They are separate because they answer different questions, and collapsing them
would hide a real distinction: CLOSE_CASE and ESCALATE both *could* legally
reach CLOSED from UNDER_REVIEW, but only one of them means "close this". An
action is not merely a transition; it is an authority claim about why.

CLOSED IS TERMINAL
------------------
There is no path out of CLOSED. Reopening a closed compliance case would
overwrite the fact that it was closed -- and that fact, with its reviewer and
timestamp, is exactly what an auditor came to see. The honest way to revisit a
closed case is a new case, which is why `Case.case_ref` exists and why nothing
here mutates history.
"""

from __future__ import annotations

from app.core.enums import CaseStatus, ReviewAction

# Which state changes are permissible at all. A state's own value is NOT in its
# set: a no-op is handled explicitly below, because "the action left the case
# where it was" and "the action moved the case" are different audit facts.
_LEGAL_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.OPEN: frozenset({CaseStatus.UNDER_REVIEW, CaseStatus.ESCALATED, CaseStatus.CLOSED}),
    CaseStatus.UNDER_REVIEW: frozenset({CaseStatus.ESCALATED, CaseStatus.SAR_REVIEW, CaseStatus.CLOSED}),
    # An escalated case can be de-escalated back to review: escalation is a
    # request for senior attention, and senior attention concluding "this needs
    # more work, not a SAR" is a legitimate outcome, not an error.
    CaseStatus.ESCALATED: frozenset({CaseStatus.UNDER_REVIEW, CaseStatus.SAR_REVIEW, CaseStatus.CLOSED}),
    # A rejected draft SAR sends the case back for more work rather than
    # stranding it -- see REJECT_DRAFT_SAR below.
    CaseStatus.SAR_REVIEW: frozenset({CaseStatus.UNDER_REVIEW, CaseStatus.ESCALATED, CaseStatus.CLOSED}),
    CaseStatus.CLOSED: frozenset(),  # terminal
}


class ActionRule:
    """One reviewer action's contract: where it may be taken from, where it
    lands, and whether it needs a target record."""

    __slots__ = ("allowed_from", "target_state", "requires_target", "target_type", "description")

    def __init__(
        self,
        *,
        allowed_from: frozenset[CaseStatus],
        target_state: CaseStatus | None,
        requires_target: bool = False,
        target_type: str | None = None,
        description: str = "",
    ) -> None:
        self.allowed_from = allowed_from
        # None means "does not change the case's state". The action still
        # records a review and an audit entry -- a decision that leaves the
        # state alone is still a decision.
        self.target_state = target_state
        self.requires_target = requires_target
        # WHICH record target_id must name. Declared here, next to
        # requires_target, so "needs a target" and "a target of what" cannot
        # drift apart -- and so a caller can be told both without
        # reimplementing this table. Null exactly when requires_target is False.
        self.target_type = target_type
        self.description = description


_ACTIVE = frozenset({CaseStatus.OPEN, CaseStatus.UNDER_REVIEW, CaseStatus.ESCALATED})

_ACTION_RULES: dict[ReviewAction, ActionRule] = {
    # --- Match adjudication. These are the ONLY legitimate route to the
    # human-only EntityMatchStatus values that Phase 3 reserved (ADR-016).
    ReviewAction.CONFIRM_MATCH: ActionRule(
        allowed_from=_ACTIVE | {CaseStatus.SAR_REVIEW},
        target_state=CaseStatus.UNDER_REVIEW,
        requires_target=True,
        target_type="EntityMatch",
        description="A human confirms an entity match the engine could only propose.",
    ),
    ReviewAction.REJECT_MATCH: ActionRule(
        allowed_from=_ACTIVE | {CaseStatus.SAR_REVIEW},
        target_state=CaseStatus.UNDER_REVIEW,
        requires_target=True,
        target_type="EntityMatch",
        description="A human rejects a proposed entity match as a false positive.",
    ),
    # --- Progressing the case.
    ReviewAction.REQUEST_INFORMATION: ActionRule(
        allowed_from=_ACTIVE | {CaseStatus.SAR_REVIEW},
        target_state=CaseStatus.UNDER_REVIEW,
        description="More information is needed before a decision can be made.",
    ),
    ReviewAction.CONTINUE_MONITORING: ActionRule(
        allowed_from=_ACTIVE,
        target_state=CaseStatus.UNDER_REVIEW,
        description="No action now; the client stays under continuous monitoring.",
    ),
    ReviewAction.ESCALATE: ActionRule(
        allowed_from=_ACTIVE | {CaseStatus.SAR_REVIEW},
        target_state=CaseStatus.ESCALATED,
        description="Raise to senior compliance.",
    ),
    # --- SAR adjudication. Only ever from SAR_REVIEW: approving a draft that
    # does not exist is meaningless, and the state IS the precondition.
    ReviewAction.APPROVE_DRAFT_SAR: ActionRule(
        allowed_from=frozenset({CaseStatus.SAR_REVIEW}),
        # Deliberately does NOT close the case. Approving a draft means it is
        # fit to file; filing is out of this system's scope, and a case that
        # closed itself on approval would assert an outcome nobody recorded.
        target_state=None,
        requires_target=True,
        target_type="SARDraft",
        description="A human approves the draft SAR. Does not file it, and does not close the case.",
    ),
    ReviewAction.REJECT_DRAFT_SAR: ActionRule(
        allowed_from=frozenset({CaseStatus.SAR_REVIEW}),
        target_state=CaseStatus.UNDER_REVIEW,
        requires_target=True,
        target_type="SARDraft",
        description="A human rejects the draft SAR; the case returns for more work.",
    ),
    ReviewAction.CLOSE_CASE: ActionRule(
        allowed_from=_ACTIVE | {CaseStatus.SAR_REVIEW},
        target_state=CaseStatus.CLOSED,
        description="A human closes the case. Terminal.",
    ),
    # --- Phase 1's original vocabulary, kept working.
    ReviewAction.ACKNOWLEDGE: ActionRule(
        allowed_from=_ACTIVE | {CaseStatus.SAR_REVIEW},
        target_state=None,
        description="Reviewer acknowledges without changing the case's state.",
    ),
    ReviewAction.REQUEST_MORE_INFO: ActionRule(
        allowed_from=_ACTIVE | {CaseStatus.SAR_REVIEW},
        target_state=CaseStatus.UNDER_REVIEW,
        description="Legacy alias of REQUEST_INFORMATION.",
    ),
    ReviewAction.APPROVE: ActionRule(
        allowed_from=frozenset({CaseStatus.SAR_REVIEW}),
        target_state=None,
        requires_target=True,
        target_type="SARDraft",
        description="Legacy generic approve; prefer APPROVE_DRAFT_SAR.",
    ),
    ReviewAction.REJECT: ActionRule(
        allowed_from=frozenset({CaseStatus.SAR_REVIEW}),
        target_state=CaseStatus.UNDER_REVIEW,
        requires_target=True,
        target_type="SARDraft",
        description="Legacy generic reject; prefer REJECT_DRAFT_SAR.",
    ),
}


class IllegalTransitionError(Exception):
    """An invalid state move. Raised, never swallowed -- a compliance workflow
    that silently ignores an impossible transition is a workflow whose recorded
    state cannot be trusted."""


class IllegalActionError(Exception):
    """The action is not permitted from the case's current state."""


def legal_transitions(state: CaseStatus) -> frozenset[CaseStatus]:
    return _LEGAL_TRANSITIONS[state]


def is_terminal(state: CaseStatus) -> bool:
    return not _LEGAL_TRANSITIONS[state]


def action_rule(action: ReviewAction) -> ActionRule:
    return _ACTION_RULES[action]


def available_actions(state: CaseStatus) -> list[ReviewAction]:
    """Which actions a reviewer may take right now. Returned by the case API so
    a caller never has to guess -- and so an eventual UI cannot offer a button
    that the server will reject."""
    return sorted(
        (a for a, rule in _ACTION_RULES.items() if state in rule.allowed_from),
        key=lambda a: a.value,
    )


def validate_transition(current: CaseStatus, target: CaseStatus) -> None:
    """Raise unless `current -> target` is permitted."""
    if current == target:
        return  # a no-op is not a transition
    allowed = _LEGAL_TRANSITIONS[current]
    if target not in allowed:
        raise IllegalTransitionError(
            f"Illegal case transition {current.value} -> {target.value}. "
            + (
                f"{current.value} is terminal; a closed case is never reopened. "
                "Open a new case referencing it instead."
                if is_terminal(current)
                else f"Legal from {current.value}: {sorted(s.value for s in allowed)}."
            )
        )


def resolve_action(current: CaseStatus, action: ReviewAction) -> CaseStatus:
    """Validate `action` from `current` and return the resulting state.

    Both gates run: the action must be permitted from this state, AND the
    resulting move must be a legal transition. The second check is not
    redundant -- it means a future edit to _ACTION_RULES cannot introduce a
    state move that _LEGAL_TRANSITIONS forbids without this raising.
    """
    rule = _ACTION_RULES.get(action)
    if rule is None:
        raise IllegalActionError(f"Unknown review action {action!r}.")

    if current not in rule.allowed_from:
        raise IllegalActionError(
            f"Action {action.value} is not permitted while the case is {current.value}. "
            f"Permitted now: {[a.value for a in available_actions(current)]}."
        )

    target = rule.target_state if rule.target_state is not None else current
    validate_transition(current, target)
    return target


__all__ = [
    "ActionRule",
    "IllegalActionError",
    "IllegalTransitionError",
    "action_rule",
    "available_actions",
    "is_terminal",
    "legal_transitions",
    "resolve_action",
    "validate_transition",
]
