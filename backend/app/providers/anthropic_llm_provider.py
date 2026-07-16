"""
AnthropicLLMProvider -- a REAL Anthropic Messages API integration.

This is a genuine network client, not a stub that pretends to call an API
(architecture requirement: "Do not create fake implementations that pretend to
call APIs"). It either places an actual HTTPS request to api.anthropic.com or
it truthfully reports NOT_CONFIGURED. There is no third path, and in
particular there is no built-in canned response: a test that needs a
deterministic model is expected to supply its own LLMProvider implementation
(see tests/test_investigation_agent.py), which is test doubling -- not
production code lying about what it did.

FOUR DECISIONS WORTH THE READ
-----------------------------

1. THE SDK IS IMPORTED LAZILY, INSIDE THE METHODS.
   `import anthropic` at module scope would make the entire application --
   ingestion, entity resolution, the deterministic risk engine, every test --
   fail to start when the SDK is absent. The one component that needs a model
   is the only component that should care whether the model SDK exists. A
   missing SDK is therefore NOT_CONFIGURED, exactly like a missing API key.

2. `thinking.display` IS PINNED TO "omitted".
   Phase 5's brief says: never store chain-of-thought. Setting `display` to
   "summarized" would stream reasoning summaries back to us, and anything we
   receive is something a future maintainer can persist. Asking the API not to
   send it at all is the enforceable version of that rule -- we cannot store
   what we never receive. "omitted" is already the default on current models;
   it is set EXPLICITLY so the intent is legible and a future default flip
   cannot silently start returning reasoning. See ADR-026.

   Thinking itself stays ON (adaptive): grounding a claim in specific evidence
   ids is precisely the kind of checking that benefits from it. We want the
   model to reason; we just never want a transcript of it in a compliance file.

3. NO `temperature` IS SENT.
   Current-generation models REJECT sampling parameters with HTTP 400 rather
   than ignoring them. `LLMInvocationResult.temperature` is therefore reported
   as None -- the honest value. See ADR-025.

4. RETRIES ARE THE SDK'S, NOT OURS.
   The official client already retries 408/409/429/5xx and connection errors
   with exponential backoff. Wrapping that in a second bespoke retry loop
   would multiply the wall-clock ceiling (`timeout x retries x retries`) and
   duplicate logic ProviderExecutionService (ADR-008) already owns for data
   providers. We configure it and get out of the way.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings, get_settings
from app.core.enums import ProviderResultStatus
from app.providers.llm_contracts import LLMInvocationResult

PROVIDER_NAME = "anthropic"

# stop_reasons that mean "there is no usable JSON object in this response".
# Each is a distinct operational fact and must not be flattened into a generic
# failure -- a refusal and a truncation call for different human responses.
_STOP_REASON_FAILURES = {
    "refusal": "The model declined to answer (safety classifier).",
    "max_tokens": (
        "The response hit max_output_tokens and was truncated mid-object, so no "
        "complete JSON report exists. Raise llm_max_output_tokens."
    ),
    "pause_turn": "The model paused the turn; no complete report was produced.",
}


class AnthropicLLMProvider:
    """Satisfies the LLMProvider Protocol (app/providers/llm_contracts.py)."""

    provider_name: str = PROVIDER_NAME

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # Configuration, never a constant. Pinning a model id in code is the
        # same class of mistake as pinning a client id.
        self.model: str = self._settings.llm_model

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    def _api_key(self) -> str | None:
        """`llm_api_key` first, then the SDK's own conventional variable.

        Honouring ANTHROPIC_API_KEY matters because it is what a developer's
        shell already exports; requiring a second name for the same secret
        invites people to paste keys into files. Nothing here logs or returns
        the value, and no key is ever committed (.env is git-ignored; only
        .env.example is tracked).
        """
        if self._settings.llm_api_key:
            return self._settings.llm_api_key
        import os

        return os.environ.get("ANTHROPIC_API_KEY") or None

    @staticmethod
    def _sdk_available() -> bool:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def is_configured(self) -> bool:
        """Both halves are required: a key with no SDK cannot call, and an SDK
        with no key cannot authenticate. Purely local -- no network."""
        return bool(self._api_key()) and self._sdk_available()

    def unconfigured_reason(self) -> str | None:
        """Which half is missing. Returned to operators so 'not configured'
        is actionable rather than a shrug."""
        if not self._sdk_available():
            return (
                "The 'anthropic' package is not installed. "
                "Install it with: pip install -r requirements.txt"
            )
        if not self._api_key():
            return "No API key configured. Set LLM_API_KEY in backend/.env " "(or export ANTHROPIC_API_KEY)."
        return None

    # ------------------------------------------------------------------ #
    # The call
    # ------------------------------------------------------------------ #

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> LLMInvocationResult:
        if not self.is_configured():
            return self._result(
                ProviderResultStatus.NOT_CONFIGURED,
                error_message=self.unconfigured_reason(),
            )

        import anthropic

        started = time.perf_counter()
        try:
            client = anthropic.Anthropic(
                api_key=self._api_key(),
                base_url=self._settings.llm_base_url or None,
                timeout=self._settings.llm_timeout_seconds,
                max_retries=self._settings.llm_max_retries,
            )

            # Streamed, then collected via get_final_message(). We do not want
            # the individual events -- we want the whole validated object. What
            # streaming buys is timeout safety: adaptive thinking on a
            # non-trivial investigation can run long, and a non-streaming
            # request that exceeds the HTTP deadline fails having produced
            # nothing and billed for everything.
            with client.messages.stream(
                model=self.model,
                max_tokens=max_output_tokens,
                # The operator channel. Assembled context goes in the user
                # turn, NEVER here -- see the module docstring of
                # app/investigation/prompts.py.
                system=system_prompt,
                thinking={"type": "adaptive", "display": "omitted"},  # decision 2
                output_config={"format": {"type": "json_schema", "schema": json_schema}},
                messages=[{"role": "user", "content": user_prompt}],
            ) as stream:
                message = stream.get_final_message()

            latency_ms = int((time.perf_counter() - started) * 1000)

            # Check stop_reason BEFORE touching content. On a refusal, content
            # is empty and indexing it raises.
            if message.stop_reason in _STOP_REASON_FAILURES:
                return self._result(
                    ProviderResultStatus.ERROR,
                    error_message=_STOP_REASON_FAILURES[message.stop_reason],
                    latency_ms=latency_ms,
                    stop_reason=message.stop_reason,
                    usage=message.usage,
                )

            # content is a list of typed blocks. Thinking blocks come first and
            # are dropped here without ever being read -- decision 2 means
            # their text is empty anyway, and this loop is the second place
            # that guarantees no reasoning reaches the database.
            text = "".join(b.text for b in message.content if b.type == "text")
            if not text.strip():
                return self._result(
                    ProviderResultStatus.NO_RESULTS,
                    error_message="Model returned no text content.",
                    latency_ms=latency_ms,
                    stop_reason=message.stop_reason,
                    usage=message.usage,
                )

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                # Should be unreachable: output_config.format constrains
                # generation to the schema. Handled anyway -- a compliance
                # component must not crash because a vendor guarantee slipped.
                return self._result(
                    ProviderResultStatus.ERROR,
                    error_message=f"Model returned non-JSON despite a JSON schema constraint: {exc}",
                    text=text,
                    latency_ms=latency_ms,
                    stop_reason=message.stop_reason,
                    usage=message.usage,
                )

            if not isinstance(parsed, dict):
                return self._result(
                    ProviderResultStatus.ERROR,
                    error_message=f"Expected a JSON object, got {type(parsed).__name__}.",
                    text=text,
                    latency_ms=latency_ms,
                    stop_reason=message.stop_reason,
                    usage=message.usage,
                )

            return self._result(
                ProviderResultStatus.SUCCESS,
                parsed=parsed,
                text=text,
                latency_ms=latency_ms,
                stop_reason=message.stop_reason,
                usage=message.usage,
            )

        # Typed exceptions, most specific first, so a transient rate limit is
        # never indistinguishable from a bad API key.
        except anthropic.RateLimitError as exc:
            return self._result(
                ProviderResultStatus.RATE_LIMITED,
                error_message=f"Rate limited after {self._settings.llm_max_retries} retries: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except anthropic.APITimeoutError as exc:
            return self._result(
                ProviderResultStatus.TIMEOUT,
                error_message=f"Timed out after {self._settings.llm_timeout_seconds}s: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except anthropic.AuthenticationError as exc:
            return self._result(
                ProviderResultStatus.NOT_CONFIGURED,
                error_message=f"API key rejected: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except anthropic.APIConnectionError as exc:
            return self._result(
                ProviderResultStatus.ERROR,
                error_message=f"Could not reach the API: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except anthropic.APIStatusError as exc:
            return self._result(
                ProviderResultStatus.ERROR,
                error_message=f"API error {exc.status_code}: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:  # never propagate past the provider boundary
            return self._result(
                ProviderResultStatus.ERROR,
                error_message=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    # ------------------------------------------------------------------ #

    def _result(
        self,
        status: ProviderResultStatus,
        *,
        parsed: dict[str, Any] | None = None,
        text: str | None = None,
        error_message: str | None = None,
        latency_ms: int = 0,
        stop_reason: str | None = None,
        usage: Any = None,
    ) -> LLMInvocationResult:
        return LLMInvocationResult(
            status=status,
            provider=self.provider_name,
            model=self.model,
            parsed=parsed,
            text=text,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
            latency_ms=latency_ms,
            temperature=None,  # decision 3 -- not sent, so not reported
            stop_reason=stop_reason,
            error_message=error_message,
            invoked_at=datetime.now(timezone.utc),
        )
