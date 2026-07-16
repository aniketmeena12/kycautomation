"""
LLM provider registry -- resolves the configured provider name to an
LLMProvider implementation.

This is the whole of "Claude/OpenAI should be interchangeable" (Phase 5 brief
SS5). Swapping vendors is:

    1. write a class satisfying the LLMProvider Protocol,
    2. add one line to _FACTORIES,
    3. set LLM_PROVIDER / LLM_MODEL in .env.

No change to the agent, the orchestrator, the prompts, the grounding
validator, the API, or the persistence layer -- none of them import a vendor
SDK or name a vendor. tests/test_investigation_agent.py proves this by running
the entire pipeline on a provider that has nothing to do with Anthropic.

THE SEAM HAS NOW BEEN TESTED FOR REAL
-------------------------------------
Phase 5 shipped with one entry and the claim that a second vendor would need no
core change. Adding Groq proved it: a new class, the one line below, and
configuration. The agent, orchestrator, prompts, grounding validator,
persistence layer, API, and report schema were untouched -- and the two vendors
differ substantially (streaming vs non-streaming, different reasoning-
suppression levers, different usage field names, one accepts `temperature` and
one rejects it outright). All of that is absorbed inside the provider, which is
exactly what the Protocol is for. See ADR-024, ADR-030.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.config import Settings, get_settings
from app.providers.anthropic_llm_provider import AnthropicLLMProvider
from app.providers.groq_llm_provider import GroqLLMProvider
from app.providers.llm_contracts import LLMProvider


class UnknownLLMProviderError(ValueError):
    """Raised at resolution time for an unregistered provider name.

    Fail-fast, mirroring the config validators in app/risk/config.py (ADR-011):
    a typo'd LLM_PROVIDER should stop the investigation immediately with a
    readable message, not silently fall back to a default the operator did not
    ask for. Quietly substituting a different model into a compliance record is
    worse than refusing to run.
    """


_FACTORIES: dict[str, Callable[[Settings], LLMProvider]] = {
    "anthropic": lambda settings: AnthropicLLMProvider(settings),
    "groq": lambda settings: GroqLLMProvider(settings),
}


def available_llm_providers() -> list[str]:
    return sorted(_FACTORIES)


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    name = (settings.llm_provider or "").strip().lower()

    factory = _FACTORIES.get(name)
    if factory is None:
        raise UnknownLLMProviderError(
            f"Unknown llm_provider {settings.llm_provider!r}. "
            f"Registered providers: {', '.join(available_llm_providers())}."
        )

    provider = factory(settings)

    # Structural check, not decoration: a Protocol only helps if something
    # actually verifies conformance. This is where a half-written provider
    # gets caught -- at resolution, with a clear error -- instead of raising
    # AttributeError deep inside an investigation.
    if not isinstance(provider, LLMProvider):
        raise UnknownLLMProviderError(f"Provider {name!r} does not satisfy the LLMProvider protocol.")
    return provider


__all__ = ["get_llm_provider", "available_llm_providers", "UnknownLLMProviderError"]
