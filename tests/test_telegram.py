"""Tests for Telegram notifications (Phase 5).

Tests formatting, authorized user restrictions, message delivery mocks,
error handling, and deduplication of notifications.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import SecretStr

from resova.config.settings import Settings
from resova.db.models import NotificationStatus
from resova.telegram.bot import TelegramNotifier, _escape_md, _format_opportunity_message
from resova.utils.errors import TelegramError


@pytest.fixture()
def tg_settings() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        telegram_bot_token=SecretStr("123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"),
        telegram_allowed_user_id=123456789,
    )


@pytest.fixture()
def disabled_tg_settings() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
    )


def test_telegram_enabled_check(tg_settings: Settings, disabled_tg_settings: Settings) -> None:
    notifier = TelegramNotifier(tg_settings)
    assert notifier.enabled is True

    disabled_notifier = TelegramNotifier(disabled_tg_settings)
    assert disabled_notifier.enabled is False


def test_escape_md() -> None:
    escaped = _escape_md("Project_Title *Bold* [Link] (Note) #1")
    assert "\\_" in escaped
    assert "\\*" in escaped
    assert "\\[" in escaped
    assert "\\(" in escaped
    assert "\\#" in escaped


def test_format_opportunity_message_arabic() -> None:
    msg = _format_opportunity_message(
        title="تصميم شبكة إطفاء",
        platform="mostaql",
        url="https://mostaql.com/projects/12345",
        score=85,
        band="strong",
        summary="مشروع متطابق مع التخصص الهندسي",
        proposal_draft="السلام عليكم، يشرفني العمل على مشروعكم...",
        language="ar",
    )
    assert "فرصة جديدة" in msg
    assert "mostaql" in msg
    assert "85/100" in msg
    assert "https://mostaql.com/projects/12345" in msg
    assert "مسودة العرض" in msg


def test_format_opportunity_message_english() -> None:
    msg = _format_opportunity_message(
        title="PowerPoint Pitch Deck Redesign",
        platform="khamsat",
        url="https://khamsat.com/community/requests/6789",
        score=75,
        band="good",
        summary="Presentation design opportunity",
        language="en",
    )
    assert "New Opportunity" in msg
    assert "khamsat" in msg
    assert "75/100" in msg


def test_send_opportunity_success(tg_settings: Settings) -> None:
    notifier = TelegramNotifier(tg_settings)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 999}}

    with patch("httpx.Client.post", return_value=mock_resp):
        res = notifier.send_opportunity(
            project_id=1,
            title="Test Project",
            platform="mostaql",
            url="https://mostaql.com/projects/1",
            score=90,
            band="strong",
        )

    assert res["status"] == NotificationStatus.sent
    assert res["message_id"] == 999
    assert res["error"] is None


def test_send_opportunity_deduplication(tg_settings: Settings) -> None:
    notifier = TelegramNotifier(tg_settings)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 1001}}

    with patch("httpx.Client.post", return_value=mock_resp):
        res1 = notifier.send_opportunity(
            project_id=42,
            title="Duplicate Test",
            platform="mostaql",
            url=None,
            score=80,
            band="good",
        )
        assert res1["status"] == NotificationStatus.sent
        assert res1.get("duplicate") is not True

        # Second send for same project_id must be skipped as duplicate
        res2 = notifier.send_opportunity(
            project_id=42,
            title="Duplicate Test",
            platform="mostaql",
            url=None,
            score=80,
            band="good",
        )
        assert res2.get("duplicate") is True


def test_send_opportunity_disabled_raises(disabled_tg_settings: Settings) -> None:
    notifier = TelegramNotifier(disabled_tg_settings)
    with pytest.raises(TelegramError, match="not configured"):
        notifier.send_opportunity(
            project_id=1,
            title="Test",
            platform="mostaql",
            url=None,
            score=70,
            band="good",
        )


def test_clickable_url_resolution_and_button(tg_settings: Settings) -> None:
    from resova.telegram.bot import _to_clickable_url

    # Relative Khamsat path with Arabic characters
    rel_khamsat = "/community/requests/801118-مطلوب-مهندس-تصميم-نظام-مكافحة-حريق-وإعداد-shop-dra"
    res_khamsat = _to_clickable_url(rel_khamsat, platform="khamsat")
    assert res_khamsat is not None
    assert res_khamsat.startswith("https://khamsat.com/community/requests/801118-")
    assert "%D9%85" in res_khamsat  # Percent-encoded Arabic for clickable link

    # Relative Mostaql path
    rel_mostaql = "/project/12345-fire-network"
    res_mostaql = _to_clickable_url(rel_mostaql, platform="mostaql")
    assert res_mostaql == "https://mostaql.com/project/12345-fire-network"

    # Notification message includes clickable markdown link
    msg = _format_opportunity_message(
        title="مطلوب مهندس إطفاء",
        platform="khamsat",
        url=rel_khamsat,
        score=90,
        band="strong",
    )
    assert "[خمسات — اضغط هنا لفتح المشروع](" in msg

    # send_opportunity attaches Inline Keyboard Button
    notifier = TelegramNotifier(tg_settings)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 888}}

    with patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        res = notifier.send_opportunity(
            project_id=801118,
            title="مطلوب مهندس",
            platform="khamsat",
            url=rel_khamsat,
            score=90,
            band="strong",
        )
        assert res["status"] == NotificationStatus.sent
        payload = mock_post.call_args[1]["json"]
        assert "reply_markup" in payload
        button = payload["reply_markup"]["inline_keyboard"][0][0]
        assert "فتح المشروع على خمسات" in button["text"]
        assert button["url"] == res_khamsat
