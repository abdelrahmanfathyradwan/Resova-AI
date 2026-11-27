"""Telegram notification service.

Sends opportunity notifications to a single authorised Telegram user.
Handles formatting, error recovery, and duplicate prevention.
Never exposes secrets in messages.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from resova.config.settings import Settings
from resova.db.models import NotificationStatus
from resova.utils.errors import TelegramError

logger = logging.getLogger(__name__)


def _to_clickable_url(url: str | None, platform: str | None = None) -> str | None:
    """Ensure a URL is absolute, canonical, and properly percent-encoded for Telegram."""
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None

    # Resolve relative paths using platform base URL
    if raw.startswith("/"):
        plat_str = str(platform).lower() if platform else ""
        raw = f"https://mostaql.com{raw}" if "mostaql" in plat_str else f"https://khamsat.com{raw}"
    elif not raw.startswith("http://") and not raw.startswith("https://"):
        plat_str = str(platform).lower() if platform else ""
        prefix = "https://mostaql.com/" if "mostaql" in plat_str else "https://khamsat.com/"
        raw = f"{prefix}{raw.lstrip('/')}"

    try:
        parts = urlsplit(raw)
        quoted_path = quote(parts.path, safe="/:@!$&'()*+,;=-_.~")
        quoted_query = quote(parts.query, safe="=&?/:@!$'()*+,;=-_.~")
        return urlunsplit((
            parts.scheme or "https",
            parts.netloc,
            quoted_path,
            quoted_query,
            parts.fragment,
        ))
    except Exception:
        return raw


def _format_opportunity_message(
    title: str,
    platform: str,
    url: str | None,
    score: int,
    band: str,
    summary: str | None = None,
    proposal_draft: str | None = None,
    confidence: float | None = None,
    rationale: str | None = None,
    language: str = "ar",
) -> str:
    """Format a Telegram notification message for an opportunity."""
    band_lower = str(band).lower()
    if "strong" in band_lower or score >= 85:
        badge = "🟢 *فرصة ممتازة*"
    elif "good" in band_lower or score >= 70:
        badge = "🟡 *فرصة جيدة جداً*"
    else:
        badge = "🔵 *فرصة واعدة*"

    clean_url = _to_clickable_url(url, platform)

    if language == "ar":
        lines = [
            "🔔 *فرصة جديدة*",
            badge,
            "",
            f"📋 *العنوان:* {_escape_md(title)}",
            f"🌐 *المنصة:* {platform}",
            f"📊 *التقييم:* {score}/100 ({band})",
        ]
        if confidence is not None:
            lines.append(f"🎯 *درجة الثقة:* {int(confidence * 100)}%")
        if clean_url:
            platform_display = "خمسات" if "khamsat" in str(platform).lower() else "مستقل"
            lines.append(f"🔗 *الرابط:* [{platform_display} — اضغط هنا لفتح المشروع]({clean_url})")
        if rationale:
            lines.append("")
            lines.append(f"💡 *سبب المطابقة:* {_escape_md(rationale)}")
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
            badge,
            "",
            f"📋 *Title:* {_escape_md(title)}",
            f"🌐 *Platform:* {platform}",
            f"📊 *Score:* {score}/100 ({band})",
        ]
        if confidence is not None:
            lines.append(f"🎯 *Confidence:* {int(confidence * 100)}%")
        if clean_url:
            platform_display = "Khamsat" if "khamsat" in str(platform).lower() else "Mostaql"
            lines.append(f"🔗 *Link:* [Open on {platform_display}]({clean_url})")
        if rationale:
            lines.append("")
            lines.append(f"💡 *Rationale:* {_escape_md(rationale)}")
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
        self._sent_proposal_draft_ids: set[int] = set()

    @property
    def enabled(self) -> bool:
        """Whether Telegram notifications are configured and enabled."""
        owner_id = self._settings.owner_chat_id
        return (
            self._settings.enable_telegram
            and self._settings.telegram_bot_token is not None
            and owner_id is not None
            and owner_id > 0
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
        confidence: float | None = None,
        rationale: str | None = None,
        language: str = "ar",
        recipients: list[int] | None = None,
    ) -> dict[str, Any]:
        """Send an opportunity notification to authorized users.

        Returns a dict with ``status``, ``message_id`` (if sent), and ``error`` (if failed).
        Prevents duplicate sends for the same ``project_id`` within the session.
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
            confidence=confidence,
            rationale=rationale,
            language=language,
        )

        clean_url = _to_clickable_url(url, platform)
        reply_markup = None
        if clean_url:
            platform_display = "خمسات" if "khamsat" in str(platform).lower() else "مستقل"
            reply_markup = {
                "inline_keyboard": [
                    [
                        {
                            "text": f"🔗 فتح المشروع على {platform_display}",
                            "url": clean_url,
                        }
                    ]
                ]
            }

        recipient_list = list(dict.fromkeys(recipients or [self._settings.owner_chat_id]))
        recipient_list = [cid for cid in recipient_list if cid is not None]

        sent_count = 0
        first_msg_id = None
        last_error = None

        for cid in recipient_list:
            try:
                res = self._send_message(message, chat_id=cid, reply_markup=reply_markup)
                sent_count += 1
                if first_msg_id is None:
                    first_msg_id = res.get("message_id")
            except Exception as exc:
                logger.error("Failed to send Telegram notification to user %d: %s", cid, exc)
                last_error = str(exc)

        if sent_count > 0:
            self._sent_project_ids.add(project_id)
            return {
                "status": NotificationStatus.sent,
                "message_id": first_msg_id,
                "sent_count": sent_count,
                "error": last_error,
            }
        return {
            "status": NotificationStatus.failed,
            "message_id": None,
            "error": last_error or "Failed to deliver to any recipient",
        }

    def send_digest(
        self,
        items: list[dict[str, Any]],
        language: str = "ar",
        recipients: list[int] | None = None,
    ) -> dict[str, Any]:
        """Send a single consolidated digest message for moderate opportunities."""
        if not self.enabled:
            return {"status": NotificationStatus.skipped, "reason": "telegram_disabled"}
        if not items:
            return {"status": "empty", "count": 0}

        lines = [
            "📋 *ملخص الفرص الواعدة (Moderate Opportunities)*",
            f"تم رصد {len(items)} فرصة جديدة محتملة تحتاج مراجعة:",
            "",
        ]
        for i, item in enumerate(items, 1):
            title = _escape_md(item.get("title", ""))
            score = item.get("score", 0)
            url = item.get("url")
            platform = item.get("platform", "")
            clean_url = _to_clickable_url(url, platform)
            if clean_url:
                lines.append(f"{i}\\. [{title}]({clean_url}) — *{score}/100* ({platform})")
            else:
                lines.append(f"{i}\\. {title} — *{score}/100* ({platform})")

        message = "\n".join(lines)
        recipient_list = list(dict.fromkeys(recipients or [self._settings.owner_chat_id]))
        recipient_list = [cid for cid in recipient_list if cid is not None]

        sent_count = 0
        first_msg_id = None
        last_error = None

        for cid in recipient_list:
            try:
                res = self._send_message(message, chat_id=cid)
                sent_count += 1
                if first_msg_id is None:
                    first_msg_id = res.get("message_id")
            except Exception as exc:
                logger.error("Failed to send Telegram digest to user %d: %s", cid, exc)
                last_error = str(exc)

        if sent_count > 0:
            return {
                "status": NotificationStatus.sent,
                "message_id": first_msg_id,
                "sent_count": sent_count,
                "count": len(items),
            }
        return {
            "status": NotificationStatus.failed,
            "error": last_error or "Failed to deliver digest to any recipient",
            "count": len(items),
        }

    def _send_message(
        self,
        text: str,
        chat_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "Markdown",
    ) -> dict[str, Any]:
        """Send a message via Telegram Bot API."""
        import httpx

        token = self._settings.telegram_bot_token
        if token is None:
            raise TelegramError("Telegram bot token is not set")

        target_chat_id = chat_id or self._settings.owner_chat_id
        if target_chat_id is None:
            raise TelegramError("Target chat_id is not specified and owner_chat_id is unset")

        secret_val = token.get_secret_value()
        api_url = f"https://api.telegram.org/bot{secret_val}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": target_chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(api_url, json=payload)
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
            safe_msg = str(exc).replace(secret_val, "***")
            raise TelegramError(f"HTTP error sending Telegram message: {safe_msg}") from exc

    def send_proposal_created_notification(
        self,
        *,
        chat_id: int,
        draft_id: int,
        project_title: str,
        proposal_title: str,
        proposal_url: str,
        max_retries: int = 2,
    ) -> dict[str, Any]:
        """Notify the proposal owner on Telegram with a direct link button.

        Uses bounded retries and per-draft deduplication. Never exposes tokens.
        """
        if not self._settings.enable_telegram or self._settings.telegram_bot_token is None:
            raise TelegramError("Telegram notifications are not configured")
        if chat_id <= 0:
            raise TelegramError("Invalid target Telegram chat_id")

        if draft_id in self._sent_proposal_draft_ids:
            logger.info("Skipping duplicate proposal notification for draft %d", draft_id)
            return {"sent": True, "message_id": None, "duplicate": True, "error": None}

        message = (
            "🎉 تم إنشاء مسودتك الفنية بنجاح!\n\n"
            f"📌 المشروع: {project_title}\n"
            f"📄 العرض: {proposal_title}\n\n"
            "يمكنك الآن فتح العرض ومراجعته وتعديل أقسامه وتصديره PDF."
        )
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": "📄 فتح العرض الفني",
                        "url": proposal_url,
                    }
                ]
            ]
        }

        last_error: str | None = None
        for attempt in range(max_retries + 1):
            try:
                res = self._send_message(
                    text=message,
                    chat_id=chat_id,
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
                self._sent_proposal_draft_ids.add(draft_id)
                return {
                    "sent": True,
                    "message_id": res.get("message_id"),
                    "duplicate": False,
                    "error": None,
                }
            except TelegramError as exc:
                last_error = str(exc)
                logger.warning(
                    "Attempt %d/%d failed sending proposal notification for draft %d: %s",
                    attempt + 1,
                    max_retries + 1,
                    draft_id,
                    last_error,
                )

        raise TelegramError(last_error or "Failed to send proposal notification")

    def send_message_with_keyboard(
        self,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any],
    ) -> dict[str, Any]:
        """Send a message with inline keyboard markup."""
        if not self.enabled:
            raise TelegramError("Telegram notifications are not configured")
        return self._send_message(text=text, chat_id=chat_id, reply_markup=reply_markup)

    def answer_callback_query(
        self,
        callback_query_id: str,
        text: str | None = None,
        show_alert: bool = False,
    ) -> dict[str, Any]:
        """Answer a Telegram callback query."""
        import httpx

        token = self._settings.telegram_bot_token
        if token is None:
            raise TelegramError("Telegram bot token is not set")

        api_url = f"https://api.telegram.org/bot{token.get_secret_value()}/answerCallbackQuery"
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = show_alert

        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(api_url, json=payload)
                response.raise_for_status()
                return {"ok": True}
        except Exception as exc:
            logger.warning("Failed to answer callback query %s: %s", callback_query_id, exc)
            return {"ok": False, "error": str(exc)}

    def send_text(self, text: str, chat_id: int | None = None) -> dict[str, Any]:
        """Send a plain text message to a specific user or the owner."""
        if not self.enabled:
            raise TelegramError("Telegram notifications are not configured")
        return self._send_message(text=text, chat_id=chat_id)
