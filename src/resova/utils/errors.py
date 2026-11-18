"""Exception hierarchy for Resova (Phase 1 / Step 2.6).

One base class keeps ``except`` clauses predictable, and one explicit flag (
:attr:`ResovaError.retryable`) tells the retry helper what may be attempted again.
Only errors that are *known* to be transient are ever retried; anything unrecognised
fails immediately so a permanent problem is never hammered.

Messages are always safe to log: they must never contain secrets (see
``docs/PROJECT_RULES.md``).
"""

from __future__ import annotations

import enum


class ResovaError(Exception):
    """Base class for every error Resova raises.

    ``retryable`` defaults to ``False``: a failure is treated as permanent unless a
    subclass (or :class:`SourceError`'s reason) says otherwise.
    """

    retryable: bool = False


class ConfigError(ResovaError):
    """Configuration is missing or invalid."""


class SourceFailureReason(enum.StrEnum):
    """Why a source call failed. The reason decides whether it can be retried."""

    auth = "auth"  #: Bad or missing credentials - permanent.
    rate_limited = "rate_limited"  #: The source asked us to slow down - transient.
    unavailable = "unavailable"  #: Network/server problem - transient.
    invalid_data = "invalid_data"  #: The payload could not be parsed - permanent.


#: Reasons that are safe to retry with backoff.
RETRYABLE_REASONS = frozenset({SourceFailureReason.rate_limited, SourceFailureReason.unavailable})


class SourceError(ResovaError):
    """A failure while reading opportunities from a source."""

    def __init__(self, message: str, *, reason: SourceFailureReason) -> None:
        super().__init__(message)
        self.reason = reason
        self.retryable = reason in RETRYABLE_REASONS


class AIError(ResovaError):
    """Scoring a project with the AI provider failed."""


class TelegramError(ResovaError):
    """Sending a notification or handling an update through Telegram failed."""


class StorageError(ResovaError):
    """Persisting or reading data failed."""


def is_retryable(error: BaseException) -> bool:
    """Return ``True`` only for Resova errors explicitly marked retryable."""
    return isinstance(error, ResovaError) and error.retryable
