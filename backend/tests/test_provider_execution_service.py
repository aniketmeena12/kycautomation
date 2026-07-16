"""ProviderExecutionService: timeout, retry, error-handling, and
not-configured short-circuit -- proven against deliberately-flaky/hanging/
raising synthetic providers, not real network calls."""

import time
from datetime import datetime, timezone

from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus
from app.providers.schemas import ProviderResult
from app.services.provider_execution_service import ProviderExecutionService


def _result(status, provider_name="test_provider"):
    return ProviderResult(
        status=status,
        provider=provider_name,
        provider_kind=ProviderKind.EXTERNAL_API,
        category=ProviderCategory.SANCTIONS,
        queried_at=datetime.now(timezone.utc),
    )


class _FlakyProvider:
    provider_name = "flaky_test"
    provider_kind = ProviderKind.EXTERNAL_API

    def __init__(self, fail_times: int):
        self.calls = 0
        self._fail_times = fail_times

    def is_configured(self):
        return True

    def do_thing(self):
        self.calls += 1
        if self.calls <= self._fail_times:
            return _result(ProviderResultStatus.ERROR)
        return _result(ProviderResultStatus.SUCCESS)


class _AlwaysFailingProvider:
    provider_name = "always_failing"
    provider_kind = ProviderKind.EXTERNAL_API

    def is_configured(self):
        return True

    def do_thing(self):
        return _result(ProviderResultStatus.ERROR)


class _HangingProvider:
    provider_name = "hanging_test"
    provider_kind = ProviderKind.EXTERNAL_API

    def is_configured(self):
        return True

    def do_thing(self):
        time.sleep(5)
        return _result(ProviderResultStatus.SUCCESS)


class _RaisingProvider:
    provider_name = "raising_test"
    provider_kind = ProviderKind.EXTERNAL_API

    def is_configured(self):
        return True

    def do_thing(self):
        raise RuntimeError("boom")


class _UnconfiguredProvider:
    provider_name = "unconfigured_test"
    provider_kind = ProviderKind.EXTERNAL_API

    def is_configured(self):
        return False

    def do_thing(self):
        raise AssertionError("must never be called when not configured")


def test_retries_transient_error_and_succeeds():
    provider = _FlakyProvider(fail_times=1)
    svc = ProviderExecutionService(max_retries=2, backoff_seconds=0.01)
    result = svc.execute(provider, provider.do_thing, category=ProviderCategory.SANCTIONS)
    assert result.status == ProviderResultStatus.SUCCESS
    assert provider.calls == 2


def test_gives_up_after_max_retries():
    provider = _AlwaysFailingProvider()
    svc = ProviderExecutionService(max_retries=2, backoff_seconds=0.01)
    result = svc.execute(provider, provider.do_thing, category=ProviderCategory.SANCTIONS)
    assert result.status == ProviderResultStatus.ERROR


def test_timeout_returns_promptly_without_blocking_on_hung_thread():
    provider = _HangingProvider()
    svc = ProviderExecutionService(max_retries=0, backoff_seconds=0.01)
    started = time.monotonic()
    result = svc.execute(
        provider, provider.do_thing, category=ProviderCategory.SANCTIONS, timeout_seconds=0.3
    )
    elapsed = time.monotonic() - started
    assert result.status == ProviderResultStatus.TIMEOUT
    assert elapsed < 2.0  # regression guard: previously blocked for the full 5s hang


def test_exception_is_converted_to_error_never_propagates():
    provider = _RaisingProvider()
    svc = ProviderExecutionService(max_retries=0)
    result = svc.execute(provider, provider.do_thing, category=ProviderCategory.SANCTIONS)
    assert result.status == ProviderResultStatus.ERROR
    assert "boom" in result.error_message


def test_not_configured_short_circuits_without_calling_operation():
    provider = _UnconfiguredProvider()
    svc = ProviderExecutionService()
    result = svc.execute(provider, provider.do_thing, category=ProviderCategory.SANCTIONS)
    assert result.status == ProviderResultStatus.NOT_CONFIGURED


def test_not_configured_is_never_retried():
    class CountingUnconfigured(_UnconfiguredProvider):
        def __init__(self):
            self.configured_checks = 0

        def is_configured(self):
            self.configured_checks += 1
            return False

    provider = CountingUnconfigured()
    svc = ProviderExecutionService(max_retries=3)
    svc.execute(provider, provider.do_thing, category=ProviderCategory.SANCTIONS)
    assert provider.configured_checks == 1  # short-circuited before any retry loop
