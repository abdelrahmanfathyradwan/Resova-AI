"""Tests for email opportunity ingestion (Phase 3).

Tests RFC 822 / MIME parsing, message-id deduplication, platform detection,
and safe defaults (disabled by default, no IMAP connection in tests).
"""

from __future__ import annotations

from resova.config.settings import Settings
from resova.db.models import Platform, SourceKind
from resova.sources.email_source import EmailSource, parse_email_message

SAMPLE_RFC822_MOSTAQL = b"""From: alerts@mostaql.com
To: user@example.com
Subject: New project on Mostaql: Fire Fighting Network Design
Message-ID: <mostaql-alert-12345@mostaql.com>
Date: Fri, 21 Nov 2026 10:00:00 +0000
Content-Type: text/plain; charset="utf-8"

A new project matching your interests was posted on Mostaql:

Title: Fire Fighting Network Design
Description: Design hydraulic calculations and fire fighting network in AutoCAD.
URL: https://mostaql.com/projects/778899-fire-network
Budget: $250 - $500
"""

SAMPLE_RFC822_KHAMSAT = b"""From: notifications@khamsat.com
To: user@example.com
Subject: =?utf-8?B?2LfYhNioINis2K/ZitivOiDYqti12YXZitmFINio2Yjarray2YjZitiq?=
Message-ID: <khamsat-req-9988@khamsat.com>
Date: Fri, 21 Nov 2026 11:00:00 +0000
Content-Type: text/plain; charset="utf-8"

New request in Khamsat community:
https://khamsat.com/community/requests/9988-deck-design
Presentation deck design.
"""


def test_parse_email_message_mostaql() -> None:
    parsed = parse_email_message(SAMPLE_RFC822_MOSTAQL)
    assert parsed["message_id"] == "<mostaql-alert-12345@mostaql.com>"
    assert "Fire Fighting" in parsed["subject"]
    assert parsed["platform"] == Platform.mostaql
    assert parsed["url"] == "https://mostaql.com/projects/778899-fire-network"


def test_parse_email_message_khamsat() -> None:
    parsed = parse_email_message(SAMPLE_RFC822_KHAMSAT)
    assert parsed["message_id"] == "<khamsat-req-9988@khamsat.com>"
    assert parsed["platform"] == Platform.khamsat
    assert "https://khamsat.com/community/requests/9988-deck-design" in parsed["url"]


def test_email_source_parse_raw_and_deduplicate() -> None:
    source = EmailSource()
    raw1 = source.parse_raw(SAMPLE_RFC822_MOSTAQL)
    assert raw1 is not None
    assert raw1.source_kind == SourceKind.alert_email
    assert "Fire Fighting" in (raw1.title or "")

    # Parsing the exact same message again must return None (Message-ID deduplication)
    raw2 = source.parse_raw(SAMPLE_RFC822_MOSTAQL)
    assert raw2 is None


def test_email_source_normalize() -> None:
    source = EmailSource()
    raw = source.parse_raw(SAMPLE_RFC822_MOSTAQL)
    assert raw is not None
    unified = source.normalize(raw)
    assert unified is not None
    assert unified.platform == Platform.mostaql
    assert unified.source_kind == SourceKind.alert_email
    assert unified.title == raw.title
    assert unified.url == "https://mostaql.com/projects/778899-fire-network"


def test_fetch_new_when_disabled_returns_empty() -> None:
    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
    )
    source = EmailSource(settings)
    assert source.fetch_new() == []
    assert source.health_check() is True
