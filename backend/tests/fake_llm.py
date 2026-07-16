"""
Deterministic LLMProvider implementations for tests.

THIS IS A TEST DOUBLE, NOT A FAKE INTEGRATION.
----------------------------------------------
The project rules forbid "fake implementations that pretend to call APIs" and
fabricated provider responses. That rule is about PRODUCTION code claiming to
have done something it did not. A test double is the opposite: it exists so a
test can assert what the system does with a KNOWN model response, and it lives
in tests/ where it can never be resolved by app/providers/llm_registry.py.

It is also the proof of vendor-neutrality. These classes have nothing to do
with Anthropic -- no SDK, no key, no HTTP -- yet the agent, the orchestrator,
the prompts, the grounding validator, and the API all run against them
unchanged. That is what "Claude/OpenAI are interchangeable" means in practice,
demonstrated rather than asserted (ADR-024).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.enums import ProviderResultStatus
from app.providers.llm_contracts import LLMInvocationResult


class RecordingLLMProvider:
    """Returns a canned parsed payload and records exactly what it was sent, so
    tests can assert on the prompts themselves (e.g. that no retrieved text
    reached the system prompt)."""

    provider_name = "test-recorder"

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        *,
        model: str = "test-model-v1",
        status: ProviderResultStatus = ProviderResultStatus.SUCCESS,
        error_message: str | None = None,
        configured: bool = True,
    ) -> None:
        self.model = model
        self._payload = payload
        self._status = status
        self._error_message = error_message
        self._configured = configured

        self.calls: list[dict[str, Any]] = []

    @property
    def last_system_prompt(self) -> str:
        return self.calls[-1]["system_prompt"]

    @property
    def last_user_prompt(self) -> str:
        return self.calls[-1]["user_prompt"]

    def is_configured(self) -> bool:
        return self._configured

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> LLMInvocationResult:
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "json_schema": json_schema,
                "max_output_tokens": max_output_tokens,
            }
        )
        return LLMInvocationResult(
            status=self._status,
            provider=self.provider_name,
            model=self.model,
            parsed=self._payload if self._status == ProviderResultStatus.SUCCESS else None,
            input_tokens=1234 if self._status == ProviderResultStatus.SUCCESS else None,
            output_tokens=567 if self._status == ProviderResultStatus.SUCCESS else None,
            latency_ms=42,
            temperature=None,
            stop_reason="end_turn" if self._status == ProviderResultStatus.SUCCESS else None,
            error_message=self._error_message,
            invoked_at=datetime.now(timezone.utc),
        )


def valid_report_payload(evidence_ids: list[int]) -> dict[str, Any]:
    """A well-formed, fully grounded report citing only real ids."""
    return {
        "summary": "The client carries an upstream sanctions label and operates in a high-risk sector.",
        "key_findings": [
            {
                "finding": "An upstream sanctions label is present on the client master record.",
                "evidence_ids": evidence_ids[:1],
                "confidence_statement": "Directly recorded; not independently verified by this system.",
            }
        ],
        "supporting_evidence": [
            {
                "finding": "The client's sector is classified high-risk.",
                "evidence_ids": evidence_ids[:1],
                "confidence_statement": "Structural attribute from the client master.",
            }
        ],
        "conflicting_evidence": [],
        "missing_information": ["No corporate registry provider is configured."],
        "reasoning": "The assessed score is driven by an upstream label plus structural attributes.",
        "recommendations": [
            {
                "action": "ENHANCED_DUE_DILIGENCE",
                "rationale": "The upstream label warrants independent verification.",
                "evidence_ids": evidence_ids[:1],
            }
        ],
        "confidence_statement": "Moderate: the evidence base is thin and largely upstream-derived.",
        "limitations": ["Sanctions evidence derives from curated demo data, not authoritative lists."],
        "citations": evidence_ids[:1],
    }


def hallucinating_report_payload(real_ids: list[int], fake_id: int = 999_999) -> dict[str, Any]:
    """Cites an evidence id that does not exist. The canonical hallucination."""
    payload = valid_report_payload(real_ids)
    payload["key_findings"].append(
        {
            "finding": "The client was named in a 2026 enforcement action by three regulators.",
            "evidence_ids": [fake_id],
            "confidence_statement": "High confidence based on the cited reporting.",
        }
    )
    payload["citations"] = [*real_ids[:1], fake_id]
    return payload
