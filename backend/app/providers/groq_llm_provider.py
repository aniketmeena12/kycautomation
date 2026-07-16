"""
GroqLLMProvider -- a REAL Groq Chat Completions integration.

This is the second implementation of the `LLMProvider` Protocol
(app/providers/llm_contracts.py), and it is the test of ADR-024's claim that
vendors are interchangeable. Adding it required a new class, one registry line,
and configuration -- and **no change** to the agent, the orchestrator, the
prompts, the grounding validator, the persistence layer, the API, or the report
schema. The claim held.

Like the Anthropic provider, it either places a genuine HTTPS request to
api.groq.com or truthfully reports NOT_CONFIGURED. There is no canned response.
The SDK is imported lazily inside the methods (ADR-023), so the application and
the whole test suite still run with `groq` uninstalled.

FOUR PLACES GROQ DIFFERS FROM ANTHROPIC, AND WHY THE CODE LOOKS DIFFERENT
-------------------------------------------------------------------------

1. NON-STREAMING.
   Groq's docs are explicit: "Streaming and tool use are not currently
   supported with Structured Outputs." The Anthropic provider streams for
   timeout safety; here streaming and schema-constrained JSON are mutually
   exclusive, and the schema guarantee is worth more than the streaming
   ergonomics. The timeout is enforced by the client instead.

2. `include_reasoning=False` REPLACES `thinking.display="omitted"`.
   ADR-026 says chain-of-thought is never stored -- enforced by never
   requesting it. Groq reaches the same end by a different lever, and getting
   this wrong would have silently broken the ADR: `reasoning_format="hidden"`
   is the obvious-looking knob, but Groq's docs state it is **not supported**
   on the gpt-oss models -- which are precisely the ones that support strict
   structured output. Worse, those models "include reasoning content in the
   `reasoning` field by default". So the default configuration of this provider
   would have returned CoT on every call. `include_reasoning=False` is the
   documented lever for gpt-oss; `_never_read_reasoning` below is the belt to
   its braces.

3. TEMPERATURE IS ACTUALLY SENT.
   ADR-025 kept the `temperature` column despite Anthropic rejecting sampling
   parameters, on the grounds that "a future provider may legitimately use
   one". This is that provider. Groq accepts `temperature`, so it is sent and
   the REAL value is recorded -- the field stops being null the moment a vendor
   genuinely has one. The ADR's design survives contact with its own
   hypothetical.

4. `strict` IS A CAPABILITY BOUNDARY, NOT A PREFERENCE.
   Groq supports `strict: true` on `openai/gpt-oss-20b` and
   `openai/gpt-oss-120b` only; other schema-capable models take best-effort
   mode. Hence `groq_strict_schema` -- see the setting's comment in config.py.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings, get_settings
from app.core.enums import ProviderResultStatus
from app.providers.llm_contracts import LLMInvocationResult

PROVIDER_NAME = "groq"

# The name attached to the schema in the request. Groq requires it; it is
# metadata for the constrained decoder, not something the model must produce.
SCHEMA_NAME = "investigation_report"

# `finish_reason` values that mean "there is no usable JSON object here".
# Each is a distinct operational fact -- a truncation and a content filter call
# for different human responses and must not be flattened together.
_FINISH_REASON_FAILURES = {
    "length": (
        "The response hit max_completion_tokens and was truncated mid-object, so no complete "
        "JSON report exists. Raise llm_max_output_tokens."
    ),
    "content_filter": "The model's content filter blocked the response.",
    "tool_calls": "The model attempted a tool call; no report was produced.",
}


class GroqLLMProvider:
    """Satisfies the LLMProvider Protocol (app/providers/llm_contracts.py)."""

    provider_name: str = PROVIDER_NAME

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        # Configuration, never a constant -- same rule as the Anthropic
        # provider and as client ids.
        self.model: str = self._settings.groq_model

    # ------------------------------------------------------------------ #
    # Configuration
    # ------------------------------------------------------------------ #

    def _api_key(self) -> str | None:
        """`groq_api_key` first, then the SDK's conventional variable.

        Honouring GROQ_API_KEY matters because it is what a developer's shell
        and the Groq SDK already expect; requiring a second name for the same
        secret invites people to paste keys into files. Nothing here logs or
        returns the value.
        """
        if self._settings.groq_api_key:
            return self._settings.groq_api_key
        import os

        return os.environ.get("GROQ_API_KEY") or None

    @staticmethod
    def _sdk_available() -> bool:
        try:
            import groq  # noqa: F401
        except ImportError:
            return False
        return True

    def is_configured(self) -> bool:
        """Key AND SDK. Purely local -- never makes a network request."""
        return bool(self._api_key()) and self._sdk_available()

    def unconfigured_reason(self) -> str | None:
        if not self._sdk_available():
            return "The 'groq' package is not installed. " "Install it with: pip install -r requirements.txt"
        if not self._api_key():
            return (
                "No Groq API key configured. Set GROQ_API_KEY in backend/.env "
                "(get one at https://console.groq.com/keys)."
            )
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
            return self._result(ProviderResultStatus.NOT_CONFIGURED, error_message=self.unconfigured_reason())

        import groq

        temperature = self._settings.llm_temperature
        started = time.perf_counter()
        try:
            client = groq.Groq(
                api_key=self._api_key(),
                base_url=self._settings.groq_base_url or None,
                timeout=self._settings.llm_timeout_seconds,
                max_retries=self._settings.llm_max_retries,
            )

            # Built as a dict so an unset temperature is genuinely ABSENT from
            # the request rather than sent as an explicit null -- the two are
            # not the same to the API.
            request: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    # The operator channel. Assembled context goes in the user
                    # turn, NEVER here -- see app/investigation/prompts.py.
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_completion_tokens": max_output_tokens,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": SCHEMA_NAME,
                        "schema": json_schema,
                        "strict": self._settings.groq_strict_schema,
                    },
                },
                # ADR-026: never receive chain-of-thought. See decision 2.
                "include_reasoning": False,
                # Structured outputs and streaming are mutually exclusive here.
                "stream": False,
            }
            if temperature is not None:
                request["temperature"] = temperature

            response = client.chat.completions.create(**request)

            latency_ms = int((time.perf_counter() - started) * 1000)
            choice = response.choices[0]
            finish_reason = choice.finish_reason

            # Check finish_reason BEFORE trusting content.
            if finish_reason in _FINISH_REASON_FAILURES:
                return self._result(
                    ProviderResultStatus.ERROR,
                    error_message=_FINISH_REASON_FAILURES[finish_reason],
                    latency_ms=latency_ms,
                    stop_reason=finish_reason,
                    usage=response.usage,
                    temperature=temperature,
                )

            # ONLY `.content` is ever read. `choice.message.reasoning` is
            # deliberately never touched -- with include_reasoning=False it
            # should be absent, and if a future model or API change starts
            # populating it anyway, this line is what keeps it out of the
            # database. ADR-026 in code rather than in a docstring.
            text = choice.message.content or ""
            if not text.strip():
                return self._result(
                    ProviderResultStatus.NO_RESULTS,
                    error_message="Model returned no text content.",
                    latency_ms=latency_ms,
                    stop_reason=finish_reason,
                    usage=response.usage,
                    temperature=temperature,
                )

            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                return self._result(
                    ProviderResultStatus.ERROR,
                    error_message=(
                        f"Model returned non-JSON despite a json_schema response_format: {exc}. "
                        f"Model {self.model!r} may not support structured outputs -- see "
                        "https://console.groq.com/docs/structured-outputs"
                    ),
                    text=text,
                    latency_ms=latency_ms,
                    stop_reason=finish_reason,
                    usage=response.usage,
                    temperature=temperature,
                )

            if not isinstance(parsed, dict):
                return self._result(
                    ProviderResultStatus.ERROR,
                    error_message=f"Expected a JSON object, got {type(parsed).__name__}.",
                    text=text,
                    latency_ms=latency_ms,
                    stop_reason=finish_reason,
                    usage=response.usage,
                    temperature=temperature,
                )

            return self._result(
                ProviderResultStatus.SUCCESS,
                parsed=parsed,
                text=text,
                latency_ms=latency_ms,
                stop_reason=finish_reason,
                usage=response.usage,
                temperature=temperature,
            )

        # Typed exceptions, most specific first -- a transient rate limit must
        # never be indistinguishable from a bad key. Groq's SDK exposes the
        # same hierarchy as Anthropic's, so this mirrors the other provider.
        except groq.RateLimitError as exc:
            return self._result(
                ProviderResultStatus.RATE_LIMITED,
                error_message=f"Rate limited after {self._settings.llm_max_retries} retries: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
                temperature=temperature,
            )
        except groq.APITimeoutError as exc:
            return self._result(
                ProviderResultStatus.TIMEOUT,
                error_message=f"Timed out after {self._settings.llm_timeout_seconds}s: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
                temperature=temperature,
            )
        except groq.AuthenticationError as exc:
            return self._result(
                ProviderResultStatus.NOT_CONFIGURED,
                error_message=f"Groq API key rejected: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
                temperature=temperature,
            )
        except groq.APIConnectionError as exc:
            return self._result(
                ProviderResultStatus.ERROR,
                error_message=f"Could not reach the Groq API: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
                temperature=temperature,
            )
        except groq.APIStatusError as exc:
            # HTTP 413 on Groq is a TOKENS-PER-MINUTE rate limit, not an
            # oversized-payload error -- its own body carries
            # `"code": "rate_limit_exceeded"`. Classifying it as ERROR would
            # tell an operator their request is malformed when in fact their
            # tier is simply too small, and would lose the retryable/
            # non-retryable distinction the status enum exists to carry.
            #
            # The counter-intuitive part, learned the hard way: Groq's TPM
            # accounting RESERVES `max_completion_tokens` up front. A ~2.8k
            # prompt with max_completion_tokens=8000 bills as ~10.8k against
            # the limit and is rejected on an 8k tier -- even though nothing
            # about the prompt is too large. The budget is
            # input + reserved output, so llm_max_output_tokens is a TPM
            # setting, not just an output cap.
            if exc.status_code == 413:
                return self._result(
                    ProviderResultStatus.RATE_LIMITED,
                    error_message=(
                        f"Groq token-per-minute limit exceeded: {exc}\n"
                        "NOTE: Groq counts input + RESERVED max_completion_tokens against TPM. "
                        f"Lower LLM_MAX_OUTPUT_TOKENS (currently "
                        f"{self._settings.llm_max_output_tokens}) so that "
                        "prompt + output fits your tier, or upgrade at "
                        "https://console.groq.com/settings/billing"
                    ),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    temperature=temperature,
                )

            # A 400 most often means the configured model does not support
            # strict structured output. Say so, rather than making the operator
            # guess from a raw vendor error.
            hint = ""
            if exc.status_code == 400:
                hint = (
                    f" -- model {self.model!r} may not support json_schema "
                    f"(strict={self._settings.groq_strict_schema}). Strict mode requires "
                    "openai/gpt-oss-120b or openai/gpt-oss-20b; set GROQ_STRICT_SCHEMA=false "
                    "for best-effort models. See https://console.groq.com/docs/structured-outputs"
                )
            return self._result(
                ProviderResultStatus.ERROR,
                error_message=f"Groq API error {exc.status_code}: {exc}{hint}",
                latency_ms=int((time.perf_counter() - started) * 1000),
                temperature=temperature,
            )
        except Exception as exc:  # never propagate past the provider boundary
            return self._result(
                ProviderResultStatus.ERROR,
                error_message=f"{type(exc).__name__}: {exc}",
                latency_ms=int((time.perf_counter() - started) * 1000),
                temperature=temperature,
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
        temperature: float | None = None,
    ) -> LLMInvocationResult:
        return LLMInvocationResult(
            status=status,
            provider=self.provider_name,
            model=self.model,
            parsed=parsed,
            text=text,
            # Groq uses OpenAI-compatible usage field names, unlike Anthropic's
            # input_tokens/output_tokens. Normalised here so the orchestrator,
            # the evaluation API, and the database never learn which vendor ran
            # -- which is what makes the two genuinely interchangeable.
            input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            latency_ms=latency_ms,
            # The real value that was sent -- ADR-025's column finally earning
            # its keep on a vendor that accepts sampling parameters.
            temperature=temperature,
            stop_reason=stop_reason,
            error_message=error_message,
            invoked_at=datetime.now(timezone.utc),
        )
