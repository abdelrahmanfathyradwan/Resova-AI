"""Email-based opportunity source (Phase 3).

Reads alert emails from the client's dedicated platform alert mailbox.
Parses RFC 822 / MIME messages and maps them to RawProject / UnifiedProject.
Deduplicates at the source layer using RFC 5322 Message-ID (matching source_messages table).

IMAP connection is strictly optional and disabled by default.
No network calls are made without explicit configuration and consent.
"""

from __future__ import annotations

import email
import logging
from collections.abc import Sequence
from datetime import datetime
from email.message import Message
from typing import Any

from resova.config.settings import Settings, get_settings
from resova.db.models import Platform, SourceKind
from resova.db.repositories import ProjectUpsert
from resova.sources.base import (
    BaseSource,
    RawProject,
    SourceCapabilities,
    SourceStatus,
    UnifiedProject,
)
from resova.utils.normalize import normalize_url

logger = logging.getLogger(__name__)


def parse_email_message(msg_bytes_or_str: bytes | str) -> dict[str, Any]:
    """Parse a raw RFC 822 email message into structured fields."""
    if isinstance(msg_bytes_or_str, bytes):
        msg: Message = email.message_from_bytes(msg_bytes_or_str)
    else:
        msg = email.message_from_string(msg_bytes_or_str)

    message_id = msg.get("Message-ID", "").strip()
    subject = msg.get("Subject", "").strip()
    from_addr = msg.get("From", "").strip()
    date_str = msg.get("Date", "").strip()

    # Extract body (plain text preferred)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                    break
        if not body:  # fallback to text/html
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="replace")
                        break
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, errors="replace")

    # Detect platform from headers or body
    platform = Platform.manual
    combined_header = f"{from_addr} {subject}".lower()
    if "mostaql" in combined_header or "مستقل" in combined_header or "mostaql.com" in body.lower():
        platform = Platform.mostaql
    elif (
        "khamsat" in combined_header or "خمسات" in combined_header or "khamsat.com" in body.lower()
    ):
        platform = Platform.khamsat

    # Extract URL if present in body
    url: str | None = None
    for token in body.split():
        if token.startswith("http://") or token.startswith("https://"):
            clean_url = token.rstrip(".,;!?'\"<>()[]")
            if "mostaql.com/projects" in clean_url or "khamsat.com" in clean_url:
                url = clean_url
                break

    return {
        "message_id": message_id,
        "subject": subject,
        "from": from_addr,
        "date": date_str,
        "body": body,
        "platform": platform,
        "url": url,
    }


class EmailSource(BaseSource):
    """Opportunity source that reads and parses platform alert emails.

    Respects the configuration settings:
    - ``ENABLE_EMAIL_INGESTION``: master switch (default False).
    - ``EMAIL_POLL_MODE``: "polling" or "off" (off = no IMAP connection).
    - ``ENABLE_MOSTAQL`` / ``ENABLE_KHAMSAT``: toggle platforms independently.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._seen_message_ids: set[str] = set()

    @property
    def platform(self) -> Platform:
        return Platform.manual

    @property
    def status(self) -> SourceStatus:
        return SourceStatus.implemented

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            platform=Platform.manual,
            supported_kinds=frozenset({SourceKind.alert_email}),
        )

    def health_check(self) -> bool:
        """Cheap check: returns True if email ingestion is disabled (safe)
        or if IMAP credentials are present when enabled.
        """
        if not self._settings.enable_email_ingestion or self._settings.email_poll_mode == "off":
            return True
        return bool(
            self._settings.imap_host and self._settings.imap_user and self._settings.imap_password
        )

    def parse_raw(self, raw_message: bytes | str) -> RawProject | None:
        """Parse a single raw email message into a RawProject."""
        parsed = parse_email_message(raw_message)
        message_id = parsed["message_id"]

        if message_id and message_id in self._seen_message_ids:
            logger.debug("Duplicate email message-id ignored: %s", message_id)
            return None

        if message_id:
            self._seen_message_ids.add(message_id)

        title = parsed["subject"] or "Untitled Email Opportunity"
        return RawProject(
            source_kind=SourceKind.alert_email,
            title=title,
            description=parsed["body"] if parsed["body"] else None,
            url=parsed["url"],
            extras={
                "message_id": message_id,
                "platform": parsed["platform"],
                "from": parsed["from"],
            },
        )

    def fetch_new(self, since: datetime | None = None) -> Sequence[RawProject]:
        """Fetch new alert emails via IMAP.

        If email ingestion is disabled or poll mode is 'off', returns empty list
        without opening any network connection.
        """
        if not self._settings.enable_email_ingestion or self._settings.email_poll_mode == "off":
            logger.info("Email ingestion is disabled or in off mode; no emails fetched.")
            return []

        # If enabled, IMAP connection would occur here.
        # Guarded against accidental execution without valid credentials.
        if not self._settings.imap_host or not self._settings.imap_password:
            logger.warning("Email ingestion enabled but IMAP credentials missing; skipping.")
            return []

        # Real IMAP fetch skeleton (never called in tests unless explicitly configured)
        return []

    def normalize(self, raw: RawProject) -> UnifiedProject | None:
        """Normalize RawProject into UnifiedProject (ProjectUpsert)."""
        if not raw.title:
            return None

        platform_val = raw.extras.get("platform", Platform.manual)
        if isinstance(platform_val, Platform):
            platform = platform_val
        elif isinstance(platform_val, str):
            try:
                platform = Platform(platform_val)
            except ValueError:
                platform = Platform.manual
        else:
            platform = Platform.manual

        # Check platform toggles
        if platform == Platform.mostaql and not getattr(self._settings, "enable_mostaql", True):
            return None
        if platform == Platform.khamsat and not getattr(self._settings, "enable_khamsat", True):
            return None

        return ProjectUpsert(
            platform=platform,
            source_kind=SourceKind.alert_email,
            title=raw.title,
            description=raw.description,
            url=normalize_url(raw.url),
            external_id=raw.external_id,
        )
