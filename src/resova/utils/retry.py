"""Bounded exponential backoff with jitter (Phase 1 / Step 2.6).

The sleep function is injectable so tests run instantly and never really wait. Only
errors marked retryable (:func:`resova.utils.errors.is_retryable`) are retried; every
other exception is re-raised on the first attempt.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from resova.utils.errors import is_retryable

#: Callback invoked just before each retry: ``(error, attempt, delay_seconds)``.
OnRetry = Callable[[BaseException, int, float], None]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How many times to retry and how long to wait in between."""

    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    #: Fraction of the delay that is randomised (0.5 = +/-50%). Keeps retries from
    #: synchronising into a thundering herd.
    jitter: float = 0.5

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must not be negative")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1")

    def delay_for(self, attempt: int, rand: Callable[[], float] = random.random) -> float:
        """Delay before retry number ``attempt`` (1-based), capped and jittered.

        ``rand`` must return a float in ``[0, 1)``; it is injectable for deterministic
        tests.
        """
        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        base = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        if not self.jitter:
            return base
        spread = base * self.jitter
        return max(0.0, base + (rand() * 2 - 1) * spread)


def call_with_retry[T](
    func: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
    on_retry: OnRetry | None = None,
) -> T:
    """Call ``func`` and retry transient failures according to ``policy``.

    ``sleep`` and ``rand`` are injectable so callers/tests control timing. The last
    error is always re-raised when the attempts are exhausted or the error is permanent.
    """
    active = policy or RetryPolicy()

    for attempt in range(1, active.attempts + 1):
        try:
            return func()
        except BaseException as error:  # noqa: BLE001 - re-raised or explicitly retried
            if attempt >= active.attempts or not is_retryable(error):
                raise
            delay = active.delay_for(attempt, rand)
            if on_retry is not None:
                on_retry(error, attempt, delay)
            sleep(delay)

    raise AssertionError("unreachable: the loop either returns or raises")  # pragma: no cover
