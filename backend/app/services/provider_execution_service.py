"""
ProviderExecutionService -- the single place every provider call goes
through, so timeout/retry/error handling is implemented once and applies
uniformly to every provider (local file-based today, a real external API in
a future phase).

Concretely implements architecture requirement 7 (provider unavailability
must not break the system): a provider that hangs, raises, or reports
NOT_CONFIGURED can never propagate an unhandled exception past this service
-- every call returns a ProviderResult, always.

Retry policy: NOT_CONFIGURED is never retried (retrying won't make a missing
API key appear). ERROR/TIMEOUT/RATE_LIMITED are retried up to `max_retries`
times with linear backoff, since those are the statuses a transient network
issue would actually produce in a future live-API provider. Proven against
a deliberately-flaky test provider in tests/test_provider_execution_service.py
-- this is real, exercised retry logic, not a decorative parameter.

Timeout is enforced with a thread-based deadline (`concurrent.futures`)
rather than trusting a provider to respect a timeout itself -- necessary
because our own providers are synchronous, and a future real HTTP provider
might not honor `timeout=` correctly either.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import TypeVar

from app.core.enums import ProviderCategory, ProviderKind, ProviderResultStatus
from app.providers.schemas import ProviderResult

T = TypeVar("T")

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RETRIES = 1
DEFAULT_BACKOFF_SECONDS = 0.2

_RETRYABLE_STATUSES = (
    ProviderResultStatus.ERROR,
    ProviderResultStatus.TIMEOUT,
    ProviderResultStatus.RATE_LIMITED,
)


class ProviderExecutionService:
    def __init__(
        self,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
        default_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._default_timeout = default_timeout_seconds

    def execute(
        self,
        provider,
        operation: Callable[[], ProviderResult[T]],
        *,
        category: ProviderCategory,
        timeout_seconds: float | None = None,
    ) -> ProviderResult[T]:
        """Runs `operation()` (a zero-arg call into one `provider` method,
        e.g. `lambda: provider.search_entity(name)`) with a timeout and
        retry policy. Never raises -- any exception becomes an ERROR
        ProviderResult."""
        timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout

        if hasattr(provider, "is_configured") and not provider.is_configured():
            return self._synthetic_result(
                provider, category, ProviderResultStatus.NOT_CONFIGURED, "Provider is not configured."
            )

        last_result: ProviderResult[T] | None = None
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            last_result = self._attempt_once(provider, operation, category, timeout)
            if last_result.status not in _RETRYABLE_STATUSES:
                return last_result
            if attempt < attempts - 1:
                time.sleep(self._backoff_seconds * (attempt + 1))

        return last_result

    def _attempt_once(
        self, provider, operation: Callable[[], ProviderResult[T]], category, timeout: float
    ) -> ProviderResult[T]:
        # Deliberately NOT a `with ThreadPoolExecutor(...) as executor:` block:
        # the context manager's __exit__ calls shutdown(wait=True), which
        # blocks until the submitted task finishes -- defeating the timeout
        # entirely for a genuinely hung call (the caller would still wait
        # the full hang duration, just with a TIMEOUT label slapped on
        # afterward). shutdown(wait=False) lets this method return the
        # moment the deadline passes; the abandoned thread is left to finish
        # or die on its own. Python cannot forcibly kill a running thread --
        # this is the standard, honest limitation of timing out synchronous
        # code, not something a nicer API could avoid.
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(operation)
        try:
            result = future.result(timeout=timeout)
            if not isinstance(result, ProviderResult):
                return self._synthetic_result(
                    provider,
                    category,
                    ProviderResultStatus.ERROR,
                    f"Provider returned a non-ProviderResult value: {type(result).__name__}",
                )
            return result
        except FutureTimeoutError:
            executor.shutdown(wait=False)
            return self._synthetic_result(
                provider, category, ProviderResultStatus.TIMEOUT, f"Timed out after {timeout}s."
            )
        except Exception as exc:  # provider raised -- never let it propagate
            executor.shutdown(wait=False)
            return self._synthetic_result(
                provider, category, ProviderResultStatus.ERROR, f"Provider raised: {exc}"
            )
        else:
            executor.shutdown(wait=False)

    @staticmethod
    def _synthetic_result(
        provider, category: ProviderCategory, status: ProviderResultStatus, message: str
    ) -> ProviderResult:
        return ProviderResult(
            status=status,
            provider=getattr(provider, "provider_name", provider.__class__.__name__),
            provider_kind=getattr(provider, "provider_kind", ProviderKind.EXTERNAL_API),
            category=category,
            error_message=message,
            queried_at=datetime.now(timezone.utc),
        )
