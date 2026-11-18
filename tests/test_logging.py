"""Tests for structured logging with secret redaction (Phase 1 / Step 2.6)."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from resova.utils.logging_setup import (
    DEFAULT_PATTERNS,
    REDACTED,
    RedactionFilter,
    configure_logging,
    redact_text,
)

TELEGRAM_TOKEN = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
OPENAI_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"


@pytest.fixture(autouse=True)
def _restore_root_logger() -> Iterator[None]:
    """Keep the root logger exactly as it was before each test."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


class TestRedactText:
    @pytest.mark.parametrize(
        "raw",
        [
            f"GET /sendMessage token={TELEGRAM_TOKEN}",
            "password=hunter2secret",
            f"Authorization: {OPENAI_KEY}",
            f"url=https://api.example.com/data?token={TELEGRAM_TOKEN}&x=1",
        ],
    )
    def test_known_secret_values_never_survive(self, raw: str) -> None:
        redacted = redact_text(raw, secrets=[TELEGRAM_TOKEN, "hunter2secret", OPENAI_KEY])
        assert TELEGRAM_TOKEN not in redacted
        assert "hunter2secret" not in redacted
        assert OPENAI_KEY not in redacted

    def test_redacts_telegram_token_shape(self) -> None:
        assert TELEGRAM_TOKEN not in redact_text(f"bot token {TELEGRAM_TOKEN}")

    def test_redacts_openai_key_shape(self) -> None:
        assert OPENAI_KEY not in redact_text(f"key {OPENAI_KEY}")

    def test_redacts_key_value_pairs(self) -> None:
        assert redact_text("password=hunter2") == "password=***"
        assert redact_text("api_key: abcdef123456") == "api_key: ***"

    def test_redacts_url_query_parameters(self) -> None:
        redacted = redact_text("https://api.example.com/data?token=abc123&x=1")
        assert redacted == "https://api.example.com/data?token=***&x=1"

    def test_leaves_ordinary_text_untouched(self) -> None:
        text = "fetched 12 new projects for mostaql"
        assert redact_text(text) == text

    def test_empty_secret_values_are_ignored(self) -> None:
        assert redact_text("nothing to hide", secrets=["", None]) == "nothing to hide"  # type: ignore[list-item]


class TestRedactionFilter:
    def _record(self, message: str, *args: Any) -> logging.LogRecord:
        return logging.LogRecord("test", logging.INFO, __file__, 1, message, args, None)

    def test_rewrites_message_so_arguments_cannot_leak(self) -> None:
        record = self._record("sending %s", f"token={TELEGRAM_TOKEN}")
        assert RedactionFilter().filter(record) is True
        assert TELEGRAM_TOKEN not in record.getMessage()
        assert record.args == ()

    def test_known_secret_value_is_removed(self) -> None:
        record = self._record("value is %s", "hunter2secret")
        RedactionFilter(secrets=["hunter2secret"]).filter(record)
        assert "hunter2secret" not in record.getMessage()

    def test_record_without_secrets_is_returned_unchanged(self) -> None:
        record = self._record("plain message")
        RedactionFilter().filter(record)
        assert record.getMessage() == "plain message"


class TestConfigureLogging:
    def test_installs_one_handler_and_sets_level(self) -> None:
        root = configure_logging("DEBUG", stream=io.StringIO())
        redacting = [h for h in root.handlers if getattr(h, "_resova_redacting", False)]
        assert len(redacting) == 1
        assert root.level == logging.DEBUG

    def test_is_idempotent(self) -> None:
        configure_logging("INFO", stream=io.StringIO())
        root = configure_logging("INFO", stream=io.StringIO())
        redacting = [h for h in root.handlers if getattr(h, "_resova_redacting", False)]
        assert len(redacting) == 1

    def test_output_is_redacted(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", stream=stream, secrets=["hunter2secret"])
        logging.getLogger("resova.test").info("password=hunter2secret token=%s", TELEGRAM_TOKEN)

        output = stream.getvalue()
        assert "hunter2secret" not in output
        assert TELEGRAM_TOKEN not in output
        assert REDACTED in output

    def test_every_pattern_is_valid_and_grouped_at_most_once(self) -> None:
        # The replacement logic relies on each pattern having 0 or 1 group.
        assert all(pattern.groups <= 1 for pattern in DEFAULT_PATTERNS)
