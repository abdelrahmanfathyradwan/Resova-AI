"""Shared utilities: exception hierarchy, retry helper and structured logging.

NOT IMPLEMENTED - Phase 1 / Step 2.6 (Utilities).

Planned content:
- Exception hierarchy: ConfigError, SourceError (AUTH | RATE_LIMITED | UNAVAILABLE |
  INVALID_DATA), AIError, TelegramError, StorageError.
- Bounded exponential backoff with jitter, injectable sleep so tests run instantly,
  and a rule that permanent errors are never retried.
- Structured logging with a redaction filter for API keys, Telegram bot tokens,
  IMAP passwords and sensitive URL query parameters.
"""

__all__: list[str] = []
