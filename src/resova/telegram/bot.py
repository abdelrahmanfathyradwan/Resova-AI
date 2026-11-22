"""Telegram notification service.

Sends opportunity notifications to a single authorised Telegram user.
Handles formatting, error recovery, and duplicate prevention.
Never exposes secrets in messages.
"""

from __future__ import annotations

import logging
from typing import Any

from resova.config.settings import Settings
from resova.db.models import NotificationStatus
from resova.utils.errors import TelegramError

logger = logging.getLogger(__name__)


def _format_opportunity_message(
    title: str,
    platform: str,
    url: str | None,
    score: int,
    band: str,
    summary: str | None = None,
    proposal_draft: str | None = None,
    language: str = "ar",
) -> str:
    """Format a Telegram notification message for an opportunity."""
    if language == "ar":
        lines = [
            "🔔 *فرصة جديدة*",
            "",
            f"📋 *العنوان:* {_escape_md(title)}",
            f"🌐 *المنصة:* {platform}",
            f"📊 *التقييم:* {score}/100 ({band})",
        ]
        if url:
            lines.append(f"🔗 *الرابط:* {url}")
        if summary:
            lines.append("")
            lines.append(f"📝 *الملخص:* {_escape_md(summary)}")
        if proposal_draft:
            lines.append("")
            lines.append("✍️ *مسودة العرض:*")
            lines.append("```")
            lines.append(proposal_draft[:2000])
            lines.append("```")
    else:
        lines = [
            "🔔 *New Opportunity*",
            "",
            f"📋 *Title:* {_escape_md(title)}",
            f"🌐 *Platform:* {platform}",
            f"📊 *Score:* {score}/100 ({band})",
        ]
        if url:
            lines.append(f"🔗 *Link:* {url}")
        if summary:
            lines.append("")
            lines.append(f"📝 *Summary:* {_escape_md(summary)}")
        if proposal_draft:
            lines.append("")
            lines.append("✍️ *Proposal Draft:*")
            lines.append("```")
            lines.append(proposal_draft[:2000])
            lines.append("```")

    return "\n".join(lines)


def _escape_md(text: str) -> str:
    """Escape Markdown special characters for Telegram."""
    for char in (
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ):
        text = text.replace(char, f"\\{char}")
    return text


class TelegramNotifier:
    """Sends notifications to a single Telegram user.

    Configuration is read from ``Settings``. The notifier never stores tokens
    in memory beyond what Settings provides via ``SecretStr``.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sent_project_ids: set[int] = set()

    @property
    def enabled(self) -> bool:
        """Whether Telegram notifications are configured and enabled."""
        return (
            self._settings.enable_telegram
            and self._settings.telegram_bot_token is not None
            and self._settings.telegram_allowed_user_id is not None
            and self._settings.telegram_allowed_user_id > 0
        )

    def send_opportunity(
        self,
        project_id: int,
        title: str,
        platform: str,
        url: str | None,
        score: int,
        band: str,
        summary: str | None = None,
        proposal_draft: str | None = None,
        language: str = "ar",
    ) -> dict[str, Any]:
        """Send an opportunity notification to the authorised user.

        Returns a dict with ``status``, ``message_id`` (if sent), and ``error`` (if failed).
        Prevents duplicate sends for the same ``project_id`` within the session.

        Raises:
            TelegramError: When Telegram is not configured or sending fails.
        """
        if not self.enabled:
            raise TelegramError("Telegram notifications are not configured")

        if project_id in self._sent_project_ids:
            logger.info("Skipping duplicate notification for project %d", project_id)
            return {
                "status": NotificationStatus.sent,
                "message_id": None,
                "error": None,
                "duplicate": True,
            }

        message = _format_opportunity_message(
            title=title,
            platform=platform,
            url=url,
            score=score,
            band=band,
            summary=summary,
            proposal_draft=proposal_draft,
            language=language,
        )

        try:
            result = self._send_message(message)
            self._sent_project_ids.add(project_id)
            return {
                "status": NotificationStatus.sent,
                "message_id": result.get("message_id"),
                "error": None,
            }
        except Exception as exc:
            logger.error("Failed to send Telegram notification for project %d: %s", project_id, exc)
            return {
                "status": NotificationStatus.failed,
                "message_id": None,
                "error": str(exc),
            }

    def _send_message(self, text: str) -> dict[str, Any]:
        """Send a message via Telegram Bot API.

        Uses httpx for HTTP calls to avoid heavy dependencies.
        """
        import httpx

        token = self._settings.telegram_bot_token
        if token is None:
            raise TelegramError("Telegram bot token is not set")

        chat_id = self._settings.telegram_allowed_user_id
        api_url = f"https://api.telegram.org/bot{token.get_secret_value()}/sendMessage"

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(
                    api_url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
                data = response.json()

                if not data.get("ok"):
                    raise TelegramError(
                        f"Telegram API error: {data.get('description', 'unknown error')}"
                    )

                return {
                    "message_id": data.get("result", {}).get("message_id"),
                    "ok": True,
                }
        except httpx.HTTPError as exc:
            raise TelegramError(f"HTTP error sending Telegram message: {exc}") from exc

    def send_text(self, text: str) -> dict[str, Any]:
        """Send a plain text message (for status updates, etc.)."""
        if not self.enabled:
            raise TelegramError("Telegram notifications are not configured")
        return self._send_message(text)
