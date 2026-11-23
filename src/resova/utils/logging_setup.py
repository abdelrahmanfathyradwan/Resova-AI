"""Structured logging with secret redaction (Phase 1 / Step 2.6).

Secrets must never reach a log file or the console. Every handler added by
:func:`configure_logging` gets a :class:`RedactionFilter` that rewrites each record
*before* it is formatted, so both the message and its arguments are scrubbed.

Two layers of defence:

1. Known secret *values* (e.g. from ``Settings``) are replaced outright.
2. Shape-based patterns catch secrets even when their value is unknown: Telegram bot
   tokens, OpenAI keys, and ``password=`` / ``token=`` / ``api_key=`` style pairs
   (including inside URLs).
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Iterable, Sequence
from typing import TextIO

REDACTED = "***"

#: Shape-based patterns. Each has at most one group holding the non-secret prefix.
DEFAULT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # key=value / key: value pairs such as "password=hunter2" or "token: abc".
    re.compile(
        r"(?i)(\b(?:password|passwd|pwd|token|api[_-]?key|secret|authorization)\b\s*[=:]\s*)"
        r"[^\s&,;]+"
    ),
    # The same names as URL query parameters: "?token=abc&x=1".
    re.compile(r"(?i)([?&](?:token|api[_-]?key|key|password|signature|sig)=)[^&\s]+"),
    # Telegram bot token: "<bot id>:<secret>".
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    # OpenAI-style API keys.
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
)


def redact_text(
    text: str,
    *,
    secrets: Iterable[str] = (),
    patterns: Sequence[re.Pattern[str]] = DEFAULT_PATTERNS,
) -> str:
    """Return ``text`` with known secret values and secret-shaped substrings removed."""
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, REDACTED)
    for pattern in patterns:
        result = pattern.sub(f"\\1{REDACTED}" if pattern.groups else REDACTED, result)
    return result


class RedactionFilter(logging.Filter):
    """Rewrite a log record in place so it can never carry a secret."""

    def __init__(
        self,
        secrets: Iterable[str] = (),
        patterns: Sequence[re.Pattern[str]] = DEFAULT_PATTERNS,
    ) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)
        self._patterns = tuple(patterns)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_text(message, secrets=self._secrets, patterns=self._patterns)
        if redacted != message:
            # Collapse to a single pre-formatted string so ``args`` cannot leak.
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(
    level: str | int = "INFO",
    *,
    stream: TextIO | None = None,
    secrets: Iterable[str] = (),
) -> logging.Logger:
    """Install one redacting stream handler on the root logger.

    Safe to call repeatedly: existing handlers are left untouched, so a second call
    does not duplicate output.
    """
    root = logging.getLogger()
    root.setLevel(level)

    existing = [h for h in root.handlers if getattr(h, "_resova_redacting", False)]
    if stream is not None and existing:
        for h in existing:
            root.removeHandler(h)
        existing = []

    if not existing:
        handler = logging.StreamHandler(stream or sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        handler.addFilter(RedactionFilter(secrets))
        handler._resova_redacting = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    return root
