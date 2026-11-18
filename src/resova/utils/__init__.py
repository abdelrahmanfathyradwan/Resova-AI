"""Shared utilities: normalization, dedup keys, errors, retry and redacting logging.

Implemented in Phase 1:

* Step 2.5: text/URL normalization (:mod:`resova.utils.normalize`) and the dedup
  identity keys (:mod:`resova.utils.identity`).
* Step 2.6: the exception hierarchy (:mod:`resova.utils.errors`), bounded backoff
  retry (:mod:`resova.utils.retry`) and the logging redaction filter
  (:mod:`resova.utils.logging_setup`).
"""

from __future__ import annotations

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
from resova.utils.identity import (
    MIN_DESCRIPTION_LENGTH,
    content_key,
    external_key,
    url_key,
)
from resova.utils.logging_setup import (
    REDACTED,
    RedactionFilter,
    configure_logging,
    redact_text,
)
from resova.utils.normalize import normalize_text, normalize_url
from resova.utils.retry import RetryPolicy, call_with_retry

__all__ = [
    "MIN_DESCRIPTION_LENGTH",
    "REDACTED",
    "AIError",
    "ConfigError",
    "RedactionFilter",
    "ResovaError",
    "RetryPolicy",
    "SourceError",
    "SourceFailureReason",
    "StorageError",
    "TelegramError",
    "call_with_retry",
    "configure_logging",
    "content_key",
    "external_key",
    "is_retryable",
    "normalize_text",
    "normalize_url",
    "redact_text",
    "url_key",
]
