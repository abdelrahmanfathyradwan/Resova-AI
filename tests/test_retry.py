"""Tests for bounded backoff + jitter retry (Phase 1 / Step 2.6).

Sleep is injected everywhere, so nothing here ever really waits.
"""

from __future__ import annotations

import pytest

from resova.utils.errors import SourceError, SourceFailureReason
from resova.utils.retry import RetryPolicy, call_with_retry

TRANSIENT = SourceFailureReason.unavailable
PERMANENT = SourceFailureReason.auth


class Recorder:
    """Collects the delays passed to the injected sleep function."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def failing(error: BaseException, times: int) -> object:
    """Return a callable that raises ``error`` the first ``times`` calls, then returns."""
    state = {"n": 0}

    def func() -> str:
        state["n"] += 1
        if state["n"] <= times:
            raise error
        return "done"

    return func


class TestRetryPolicy:
    def test_exponential_growth_without_jitter(self) -> None:
        policy = RetryPolicy(attempts=5, base_delay=1.0, max_delay=100.0, jitter=0.0)
        assert [policy.delay_for(n) for n in range(1, 5)] == [1.0, 2.0, 4.0, 8.0]

    def test_delay_is_capped_at_max(self) -> None:
        policy = RetryPolicy(attempts=10, base_delay=1.0, max_delay=4.0, jitter=0.0)
        assert policy.delay_for(6) == 4.0

    def test_jitter_stays_within_bounds(self) -> None:
        policy = RetryPolicy(base_delay=2.0, max_delay=100.0, jitter=0.5)
        assert policy.delay_for(1, lambda: 0.0) == pytest.approx(1.0)  # -50%
        assert policy.delay_for(1, lambda: 0.5) == pytest.approx(2.0)  # mid
        assert policy.delay_for(1, lambda: 1.0) == pytest.approx(3.0)  # +50%

    def test_never_returns_negative_delay(self) -> None:
        policy = RetryPolicy(base_delay=0.0, jitter=1.0)
        assert policy.delay_for(1, lambda: 0.0) == 0.0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"attempts": 0},
            {"base_delay": -1},
            {"max_delay": -1},
            {"jitter": -0.1},
            {"jitter": 1.1},
        ],
    )
    def test_invalid_policy_rejected(self, kwargs: dict[str, float]) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)  # type: ignore[arg-type]

    def test_attempt_below_one_rejected(self) -> None:
        with pytest.raises(ValueError):
            RetryPolicy().delay_for(0)


class TestCallWithRetry:
    def test_returns_immediately_without_sleeping(self) -> None:
        sleep = Recorder()
        result = call_with_retry(lambda: "ok", sleep=sleep)
        assert result == "ok"
        assert sleep.delays == []

    def test_retries_transient_errors_then_succeeds(self) -> None:
        sleep = Recorder()
        error = SourceError("temporary", reason=TRANSIENT)
        result = call_with_retry(failing(error, times=2), sleep=sleep)
        assert result == "done"
        assert len(sleep.delays) == 2  # slept once per retry

    def test_never_retries_permanent_errors(self) -> None:
        sleep = Recorder()
        error = SourceError("bad credentials", reason=PERMANENT)
        with pytest.raises(SourceError):
            call_with_retry(failing(error, times=5), sleep=sleep)
        assert sleep.delays == []

    def test_never_retries_unknown_exceptions(self) -> None:
        sleep = Recorder()

        def boom() -> None:
            raise ValueError("plain bug")

        with pytest.raises(ValueError):
            call_with_retry(boom, sleep=sleep)
        assert sleep.delays == []

    def test_raises_after_attempts_are_exhausted(self) -> None:
        sleep = Recorder()
        policy = RetryPolicy(attempts=3, base_delay=0.0, jitter=0.0)
        error = SourceError("still down", reason=TRANSIENT)
        with pytest.raises(SourceError):
            call_with_retry(failing(error, times=10), policy=policy, sleep=sleep)
        assert len(sleep.delays) == 2  # attempts - 1

    def test_on_retry_callback_receives_error_attempt_and_delay(self) -> None:
        seen: list[tuple[BaseException, int, float]] = []
        policy = RetryPolicy(attempts=2, base_delay=1.0, jitter=0.0)
        error = SourceError("down", reason=TRANSIENT)
        call_with_retry(
            failing(error, times=1),
            policy=policy,
            sleep=Recorder(),
            on_retry=lambda exc, attempt, delay: seen.append((exc, attempt, delay)),
        )
        assert len(seen) == 1
        assert seen[0][0] is error
        assert seen[0][1] == 1
        assert seen[0][2] == 1.0

    def test_delay_is_injectable_and_deterministic(self) -> None:
        sleep = Recorder()
        policy = RetryPolicy(attempts=3, base_delay=2.0, jitter=0.0)
        call_with_retry(
            failing(SourceError("x", reason=TRANSIENT), times=2), policy=policy, sleep=sleep
        )
        assert sleep.delays == [2.0, 4.0]
