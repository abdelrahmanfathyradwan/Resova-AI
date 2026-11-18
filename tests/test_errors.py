"""Tests for the exception hierarchy (Phase 1 / Step 2.6)."""

from __future__ import annotations

import pytest

from resova.utils.errors import (
    AIError,
    ConfigError,
    ResovaError,
    SourceError,
    SourceFailureReason,
    StorageError,
    TelegramError,
    is_retryable,
)

ALL_ERRORS = (ConfigError, SourceError, AIError, TelegramError, StorageError)


@pytest.mark.parametrize("error_type", ALL_ERRORS)
def test_every_error_inherits_from_the_base(error_type: type[ResovaError]) -> None:
    assert issubclass(error_type, ResovaError)
    assert issubclass(error_type, Exception)


@pytest.mark.parametrize(
    "reason, expected",
    [
        (SourceFailureReason.rate_limited, True),
        (SourceFailureReason.unavailable, True),
        (SourceFailureReason.auth, False),
        (SourceFailureReason.invalid_data, False),
    ],
)
def test_source_error_retryable_by_reason(reason: SourceFailureReason, expected: bool) -> None:
    error = SourceError("boom", reason=reason)
    assert error.reason is reason
    assert error.retryable is expected
    assert is_retryable(error) is expected


@pytest.mark.parametrize("error_type", [ConfigError, AIError, TelegramError, StorageError])
def test_non_source_errors_are_not_retryable(error_type: type[ResovaError]) -> None:
    assert is_retryable(error_type("boom")) is False


def test_unknown_exceptions_are_not_retryable() -> None:
    assert is_retryable(ValueError("plain")) is False
    assert is_retryable(ConnectionError()) is False


def test_message_is_preserved() -> None:
    assert str(SourceError("could not reach the mailbox", reason=SourceFailureReason.unavailable))
