"""
The Groq LLM provider, and the vendor-swap it proves.

Phase 5 claimed (ADR-024) that a second vendor would need a new class, a
registry line, and configuration -- and no change to the agent, orchestrator,
prompts, grounding validator, persistence, API, or report schema. These tests
are that claim being cashed in: the same `InvestigationOrchestrator` and the
same `JSON_SCHEMA` run on Groq, and the assertions below are about the SEAM,
not about Groq.

No test here makes a network call. Request *shape* is verified against the
installed SDK by AST and signature introspection -- which is what catches the
class of bug a test double structurally cannot: a parameter the vendor does not
accept.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

from app.core.config import Settings
from app.core.enums import ProviderResultStatus
from app.investigation.schemas import JSON_SCHEMA
from app.providers.groq_llm_provider import GroqLLMProvider
from app.providers.llm_contracts import LLMProvider
from app.providers.llm_registry import available_llm_providers, get_llm_provider

PROVIDER_PATH = pathlib.Path(__file__).resolve().parents[1] / "app" / "providers" / "groq_llm_provider.py"


def _create_call_kwargs() -> dict[str, ast.expr]:
    """Keyword args of the real `client.chat.completions.create(**request)`
    call, plus the keys of the `request` dict it is built from.

    AST, not grep: a substring search matches prose in docstrings and comments,
    which is how the first version of the equivalent Anthropic test passed for
    the wrong reason.
    """
    tree = ast.parse(PROVIDER_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        # `request: dict[str, Any] = {...}` is an AnnAssign (annotated), not an
        # Assign. Both are handled so a future de-annotation doesn't silently
        # turn every assertion below into a no-op.
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue

        for target in targets:
            if isinstance(target, ast.Name) and target.id == "request" and isinstance(node.value, ast.Dict):
                return {
                    k.value: v
                    for k, v in zip(node.value.keys, node.value.values)
                    if isinstance(k, ast.Constant)
                }
    raise AssertionError("Could not find the Groq request dict to inspect.")


# --------------------------------------------------------------------- #
# Registry + Protocol -- the seam
# --------------------------------------------------------------------- #


def test_groq_is_registered_and_anthropic_still_is():
    """Task 7: keep Anthropic support intact."""
    assert available_llm_providers() == ["anthropic", "groq"]


def test_groq_satisfies_the_llm_provider_protocol():
    assert isinstance(GroqLLMProvider(Settings()), LLMProvider)


def test_registry_resolves_groq_from_configuration():
    provider = get_llm_provider(Settings(llm_provider="groq", groq_model="openai/gpt-oss-20b"))
    assert provider.provider_name == "groq"
    assert provider.model == "openai/gpt-oss-20b"


def test_registry_still_resolves_anthropic():
    provider = get_llm_provider(Settings(llm_provider="anthropic"))
    assert provider.provider_name == "anthropic"
    assert provider.model == "claude-opus-4-8"


def test_provider_selection_is_case_and_whitespace_tolerant():
    assert get_llm_provider(Settings(llm_provider="  GROQ ")).provider_name == "groq"


def test_groq_model_is_configuration_not_a_constant():
    """Hardcoding a model id is the same class of mistake as hardcoding a
    client id -- and it must stay false for the second vendor too."""
    assert GroqLLMProvider(Settings(groq_model="some/other-model")).model == "some/other-model"


# --------------------------------------------------------------------- #
# Configuration + graceful degradation
# --------------------------------------------------------------------- #


def test_groq_reports_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    provider = GroqLLMProvider(Settings(groq_api_key=None))

    assert not provider.is_configured()
    result = provider.complete_json(
        system_prompt="s", user_prompt="u", json_schema=JSON_SCHEMA, max_output_tokens=100
    )
    assert result.status == ProviderResultStatus.NOT_CONFIGURED
    assert result.parsed is None
    assert "GROQ_API_KEY" in result.error_message
    assert result.provider == "groq"


def test_groq_falls_back_to_the_conventional_env_var(monkeypatch):
    """A developer's shell already exports GROQ_API_KEY; requiring a second
    name for the same secret invites people to paste keys into files."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_environment")
    assert GroqLLMProvider(Settings(groq_api_key=None)).is_configured()


def test_explicit_setting_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_environment")
    provider = GroqLLMProvider(Settings(groq_api_key="gsk_from_settings"))
    assert provider._api_key() == "gsk_from_settings"


def test_unconfigured_reason_is_actionable(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    reason = GroqLLMProvider(Settings(groq_api_key=None)).unconfigured_reason()
    assert "console.groq.com" in reason  # tells the operator where to get one


# --------------------------------------------------------------------- #
# Request shape -- verified against the INSTALLED SDK
# --------------------------------------------------------------------- #


def test_every_request_parameter_is_accepted_by_the_installed_sdk():
    """The bug class a test double cannot catch: a parameter the vendor does
    not accept fails only at runtime, against the real API."""
    groq = pytest.importorskip("groq")
    sig = inspect.signature(groq.Groq(api_key="gsk_placeholder").chat.completions.create)

    for name in _create_call_kwargs():
        assert (
            name in sig.parameters
        ), f"{name!r} is sent to Groq but is not a parameter of chat.completions.create."


def test_the_report_schema_is_valid_for_groq_strict_mode():
    """Groq strict mode: "All fields must be `required` and objects must set
    `additionalProperties: false`". The Phase 5 schema already complied -- this
    pins that it keeps complying, so a future schema edit cannot silently break
    the Groq path while leaving Anthropic green.
    """

    def check(node: dict, path: str = "root") -> None:
        if node.get("type") != "object":
            return
        assert node.get("additionalProperties") is False, f"{path}: additionalProperties must be false"
        properties = set(node.get("properties", {}))
        required = set(node.get("required", []))
        assert properties == required, (
            f"{path}: strict mode requires every property to be required. "
            f"Missing from required: {sorted(properties - required)}"
        )
        for name, child in node.get("properties", {}).items():
            if child.get("type") == "object":
                check(child, f"{path}.{name}")
            elif child.get("type") == "array" and isinstance(child.get("items"), dict):
                check(child["items"], f"{path}.{name}[]")

    check(JSON_SCHEMA)


def test_groq_uses_json_schema_constrained_decoding():
    """The report schema must be enforced at GENERATION time, exactly as on the
    Anthropic path -- not requested in prose and hoped for."""
    response_format = _create_call_kwargs()["response_format"]
    rendered = ast.dump(response_format)

    assert "'json_schema'" in rendered
    assert (
        "'json_object'" not in rendered
    ), "json_object is JSON mode without a schema -- a strictly weaker guarantee."


def test_groq_never_requests_reasoning():
    """ADR-026 on a second vendor, via a different lever.

    This is the subtle one. `reasoning_format='hidden'` is the obvious knob but
    is NOT supported on the gpt-oss models -- the only ones with strict
    structured output -- and those models return reasoning BY DEFAULT. So the
    correct lever is include_reasoning=False, and getting it wrong would have
    silently returned chain-of-thought on every call.
    """
    kwargs = _create_call_kwargs()
    assert "include_reasoning" in kwargs, "gpt-oss models return reasoning by default (ADR-026)."
    assert ast.dump(kwargs["include_reasoning"]) == ast.dump(ast.Constant(False))

    # reasoning_format would 400 on gpt-oss; it must not be sent.
    assert "reasoning_format" not in kwargs


def test_groq_never_reads_the_reasoning_field():
    """Belt to include_reasoning=False's braces: even if a future model or API
    change populates `reasoning` anyway, nothing reads it into the database."""
    source = PROVIDER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr != "reasoning", (
                "The provider reads `.reasoning` from the response. "
                "Chain-of-thought must never be read or stored (ADR-026)."
            )


def test_groq_does_not_stream():
    """Groq's docs: streaming and structured outputs are mutually exclusive.
    The schema guarantee is worth more than the streaming ergonomics."""
    stream = _create_call_kwargs().get("stream")
    assert stream is not None
    assert ast.dump(stream) == ast.dump(ast.Constant(False))


# --------------------------------------------------------------------- #
# Temperature -- ADR-025's column earning its keep
# --------------------------------------------------------------------- #


def test_groq_sends_temperature_unlike_anthropic():
    """ADR-025 kept the temperature column despite Anthropic rejecting sampling
    parameters, reasoning that "a future provider may legitimately use one".
    Groq is that provider -- so the field stops being null."""
    source = PROVIDER_PATH.read_text(encoding="utf-8")
    assert 'request["temperature"] = temperature' in source


def test_an_unset_temperature_is_absent_from_the_request_not_null():
    """Omitting a parameter and sending an explicit null are not the same thing
    to the API."""
    source = PROVIDER_PATH.read_text(encoding="utf-8")
    assert "if temperature is not None:" in source


def test_temperature_defaults_to_zero_for_least_variance():
    """A re-run should differ because the evidence changed, not because the
    sampler rolled differently."""
    assert Settings().llm_temperature == 0.0


def test_anthropic_still_sends_no_temperature_after_the_groq_change():
    """Regression: adding a vendor that DOES accept sampling parameters must
    not leak one into the vendor that rejects them with HTTP 400."""
    anthropic_source = (
        pathlib.Path(__file__).resolve().parents[1] / "app" / "providers" / "anthropic_llm_provider.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(anthropic_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "stream":
            sent = {kw.arg for kw in node.keywords}
            assert "temperature" not in sent
            assert "top_p" not in sent
            return
    raise AssertionError("Could not find the Anthropic stream call.")


# --------------------------------------------------------------------- #
# ADR-023: the SDK stays optional
# --------------------------------------------------------------------- #


def test_the_groq_sdk_is_imported_lazily():
    """A module-scope `import groq` would make the SDK a hard dependency of the
    entire application -- ingestion, the risk engine, and all 376 tests would
    fail to start without it."""
    tree = ast.parse(PROVIDER_PATH.read_text(encoding="utf-8"))
    for node in tree.body:  # module level only
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                assert not name.startswith(
                    "groq"
                ), "groq is imported at module scope; it must be lazy (ADR-023)."


def test_no_component_outside_the_provider_layer_mentions_groq():
    """Task 6, stated as a test: the Investigation Agent works on Groq with NO
    change outside the provider layer.

    If any of these modules had to learn the word "groq", the Protocol would
    have failed and ADR-024 would be marketing rather than architecture.
    """
    app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
    allowed = {
        app_dir / "providers" / "groq_llm_provider.py",
        app_dir / "providers" / "llm_registry.py",  # the one registry line
        app_dir / "core" / "config.py",  # configuration
    }

    offenders = [
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
        if path not in allowed and "groq" in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == [], f"These modules mention Groq but should be vendor-agnostic: {offenders}"


# --------------------------------------------------------------------- #
# The agent, running on Groq
# --------------------------------------------------------------------- #


def test_orchestrator_selects_groq_from_configuration_alone(db_session):
    """The default construction path -- no injected agent -- resolves Groq
    purely from settings. This is what `LLM_PROVIDER=groq` in .env does."""
    from app.services.investigation_service import InvestigationOrchestrator

    orchestrator = InvestigationOrchestrator(
        db_session, settings=Settings(llm_provider="groq", groq_model="openai/gpt-oss-120b")
    )
    status = orchestrator.agent_status()

    assert status["provider"] == "groq"
    assert status["model"] == "openai/gpt-oss-120b"
    assert status["prompt_version"] == "v1"  # unchanged by the vendor swap


def test_an_investigation_on_groq_without_a_key_fails_honestly(db_session, monkeypatch):
    """The honest-degradation guarantee must survive the vendor swap: no key
    means a recorded FAILED, never a fabricated report."""
    from app.core.enums import InvestigationStatus
    from app.ingestion.commands import ingest_dataset
    from app.services.investigation_service import InvestigationOrchestrator

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    ingest_dataset(db_session, "clients")

    orchestrator = InvestigationOrchestrator(
        db_session, settings=Settings(llm_provider="groq", groq_api_key=None)
    )
    investigation = orchestrator.run_for_client(3, trigger_reason="groq degradation test")

    assert investigation.status == InvestigationStatus.FAILED
    assert investigation.report_json is None
    assert investigation.summary is None
    assert investigation.llm_provider == "groq"
    assert investigation.llm_model == "openai/gpt-oss-120b"  # still recorded
    assert "GROQ_API_KEY" in investigation.error_message
