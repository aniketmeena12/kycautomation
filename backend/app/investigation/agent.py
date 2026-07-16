"""
InvestigationAgent -- the only component in this project that calls an LLM.

Deliberately small. It builds two prompts, makes one call, parses, and
validates. It does not decide anything, does not touch the database, and does
not know what a client is. Everything upstream (context assembly) and
downstream (persistence, status, audit) belongs to the orchestrator.

That narrowness is the point. The core design principle says an LLM must never
be the authority for a risk score, and the strongest way to keep that true is
to make the LLM-touching component structurally incapable of it: no Session, no
RiskEngine, no repository, no writer of any kind. tests/test_investigation_agent.py
parses this module's AST and asserts it imports none of them -- the same
enforcement Phase 4 applies in reverse to app/risk/engine.py (which is asserted
to import no model SDK). The two tests meet in the middle and pin the boundary
from both sides.

THREE VALIDATION GATES, EACH CATCHING WHAT THE LAST CANNOT
-----------------------------------------------------------
  1. JSON Schema (provider-side). Constrains generation: shape, required
     fields, and the recommendation `enum`. Cannot express "cite a real id".
  2. Pydantic (here). Types, coercion, and the action vocabulary again. Catches
     a provider whose constrained-output mode is weaker than advertised.
  3. Grounding (grounding.py). Semantic truth: do the cited ids exist? This is
     the only gate that can catch a hallucination, and it is deterministic code.

None is redundant. A report that passes 1 and 2 and fails 3 is well-formed,
correctly typed, and fabricated.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.core.enums import ProviderResultStatus
from app.investigation.grounding import GroundingReport, validate_report
from app.investigation.prompts import PROMPT_VERSION, build_system_prompt, build_user_prompt
from app.investigation.schemas import (
    JSON_SCHEMA,
    InvestigationContext,
    InvestigationReport,
)
from app.providers.llm_contracts import LLMInvocationResult, LLMProvider


class AgentRunResult:
    """Everything one agent run produced, successful or not.

    A failed run is a first-class result, not an exception: "the provider was
    not configured" is an ordinary operational fact that the orchestrator must
    persist and a reviewer must see. Raising would push a coverage gap into a
    stack trace, where it becomes a 500 instead of a record.
    """

    def __init__(
        self,
        *,
        invocation: LLMInvocationResult,
        report: InvestigationReport | None = None,
        grounding: GroundingReport | None = None,
        prompt_version: str = PROMPT_VERSION,
        error: str | None = None,
    ) -> None:
        self.invocation = invocation
        self.report = report
        self.grounding = grounding
        self.prompt_version = prompt_version
        self.error = error

    @property
    def succeeded(self) -> bool:
        """A report exists and is well-formed.

        Note this is TRUE even when grounding failed. A report that cites a
        fabricated id was still generated, and it must be persisted and shown
        -- flagged -- because "the model hallucinated on this client" is
        precisely what a reviewer needs to know. Hiding it behind a failure
        status would make the hallucination invisible. `grounding.passed` is
        the separate, honest signal for whether to believe the content.
        """
        return self.report is not None


class InvestigationAgent:
    def __init__(self, provider: LLMProvider, *, max_output_tokens: int = 8000) -> None:
        self._provider = provider
        self._max_output_tokens = max_output_tokens

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    @property
    def model(self) -> str:
        return self._provider.model

    def is_configured(self) -> bool:
        return self._provider.is_configured()

    def investigate(self, context: InvestigationContext) -> AgentRunResult:
        """One investigation. Never raises."""
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(context)

        invocation = self._provider.complete_json(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            json_schema=JSON_SCHEMA,
            max_output_tokens=self._max_output_tokens,
        )

        if invocation.status != ProviderResultStatus.SUCCESS:
            # Provider unavailable/timed out/rate-limited/refused. No report,
            # and emphatically no placeholder standing in for one.
            return AgentRunResult(
                invocation=invocation,
                error=invocation.error_message or f"LLM provider returned {invocation.status.value}.",
            )

        if invocation.parsed is None:
            return AgentRunResult(
                invocation=invocation, error="Provider reported SUCCESS but returned no parsed object."
            )

        # Gate 2.
        try:
            report = InvestigationReport.model_validate(invocation.parsed)
        except ValidationError as exc:
            return AgentRunResult(
                invocation=invocation,
                error=f"Model output failed schema validation: {exc.error_count()} error(s). {exc}",
            )

        # Gate 3.
        grounding = validate_report(report, context)

        return AgentRunResult(invocation=invocation, report=report, grounding=grounding)


__all__ = ["AgentRunResult", "InvestigationAgent"]
