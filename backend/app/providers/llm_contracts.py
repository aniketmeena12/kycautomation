"""
LLM provider contract -- the seam that makes the Investigation Agent
vendor-neutral (Phase 5 brief SS5: "Claude/OpenAI should be interchangeable.
Never hardcode one model.").

Deliberately shaped like app/providers/contracts.py rather than inventing a
second provider idiom:

  * a runtime-checkable Protocol, not an ABC -- an implementation only needs
    to structurally satisfy the interface, so a vendor SDK's own base classes
    never fight ours;
  * `is_configured()` on every provider, so "no API key" is a state the caller
    handles, not an exception it catches;
  * the SAME ProviderResultStatus vocabulary the data providers already use.
    An LLM that times out and a sanctions API that times out are the same kind
    of fact about coverage, and a compliance system that reports them
    differently is a system with two half-tested error paths. Reusing the enum
    means Phase 2's graceful-degradation guarantees extend to Phase 5 for free.

ONE METHOD, NOT A CHAT API
--------------------------
`complete_json` is the only capability this project needs: given instructions
and a JSON Schema, return an object matching that schema. Exposing raw
free-text chat instead would invite an unvalidated string into a compliance
record. Narrowing the contract to structured output is what makes "every
important AI-generated output should be structured and validated" enforceable
at the type level rather than by convention.

It is also what keeps vendors genuinely interchangeable: every major provider
implements constrained JSON output (Anthropic's `output_config.format`,
OpenAI's `response_format`), so a second implementation is a new class in this
package -- no change to the agent, the orchestrator, or the prompts.

WHAT THIS CONTRACT DELIBERATELY DOES NOT EXPOSE
-----------------------------------------------
There is no `temperature` parameter and no way to retrieve a chain of thought.
Both omissions are load-bearing, not oversights -- see ADR-025 and ADR-026.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.core.enums import ProviderResultStatus


class LLMInvocationResult(BaseModel):
    """The outcome of exactly one model call.

    Carries the operational metadata Phase 5 (brief SS10) requires -- model,
    provider, latency, tokens -- alongside the payload. Like ProviderResult,
    this is returned on EVERY path: a provider that is unconfigured, times out,
    is rate-limited, or raises returns one of these with a non-SUCCESS status.
    It never raises at the caller.
    """

    status: ProviderResultStatus
    provider: str
    model: str

    # Populated only on SUCCESS. `parsed` is the schema-validated object; `text`
    # is the raw response body, kept for the audit trail so a report can be
    # re-examined exactly as the model returned it.
    parsed: dict[str, Any] | None = None
    text: str | None = None

    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0

    # `None` on every current-generation Anthropic model, which REJECTS
    # sampling parameters outright (HTTP 400) rather than defaulting them.
    # Recording null is the honest answer to "what temperature was used?" --
    # inventing a plausible-looking 0.0 would be fabricating a request
    # parameter that was never sent. See ADR-025.
    temperature: float | None = None

    stop_reason: str | None = None
    error_message: str | None = None
    invoked_at: datetime = Field(default_factory=lambda: datetime.now(tz=None))

    @property
    def total_tokens(self) -> int | None:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)


@runtime_checkable
class LLMProvider(Protocol):
    """Structural interface every LLM backend must satisfy.

    `model` is an instance attribute read from configuration, never a constant
    baked into an implementation -- the anti-hardcoding rule that forbids
    pinning a client id applies just as much to pinning a model id.
    """

    provider_name: str
    model: str

    def is_configured(self) -> bool:
        """True only when this provider could actually place a call right now
        (credentials present AND the vendor SDK importable). Must never make a
        network request -- callers use this on read paths."""
        ...

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> LLMInvocationResult:
        """Run one constrained-output completion.

        `system_prompt` carries operator instructions; `user_prompt` carries
        the assembled investigation context. Implementations MUST keep those
        two channels separate -- collapsing retrieved data into the operator
        channel is how retrieved text acquires operator authority (Phase 5
        brief SS12).

        Must not raise. Every failure is a non-SUCCESS LLMInvocationResult.
        """
        ...
