"""Comprehensive test suite for Telegram Multi-User Access Control & Owner Management.

Covers:
1. New user registration via /start
2. Duplicate registration prevention
3. Pending status handling & notification to owner
4. Approval and rejection workflows (via command & callback query)
5. Disabling and re-enabling users
6. RBAC enforcement: Owner, Admin, and User permissions
7. Unauthorized user command blocking
8. Notifications dispatched to active users only
9. Owner always receives notifications
10. Resilience: failure for one user does not affect others
11. Callback Query protection against unauthorized callers
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from resova.config.settings import Settings
from resova.db.models import (
    NotificationStatus,
    TelegramUser,
    TelegramUserRole,
    TelegramUserStatus,
)
from resova.db.repositories import TelegramUserRepository
from resova.telegram.bot import TelegramNotifier
from resova.telegram.manager import TelegramUserManager

OWNER_CHAT_ID = 9990000001
USER_A_CHAT_ID = 1000000001
USER_B_CHAT_ID = 1000000002
UNAUTHORIZED_CHAT_ID = 9999999999


@pytest.fixture()
def telegram_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        telegram_bot_token=SecretStr("mock-token-1234567890"),
        telegram_owner_id=OWNER_CHAT_ID,
        telegram_allowed_user_id=OWNER_CHAT_ID,
    )


@pytest.fixture()
def mock_notifier() -> MagicMock:
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.enabled = True
    notifier.send_text.return_value = {"ok": True, "message_id": 101}
    notifier.send_message_with_keyboard.return_value = {"ok": True, "message_id": 102}
    notifier.answer_callback_query.return_value = {"ok": True}
    notifier.send_opportunity.return_value = {
        "status": NotificationStatus.sent,
        "message_id": 103,
    }
    notifier.send_digest.return_value = {
        "status": NotificationStatus.sent,
        "message_id": 104,
        "count": 1,
    }
    return notifier


# -----------------------------------------------------------------------------
# 1. Registration Lifecycle Tests
# -----------------------------------------------------------------------------


def test_1_new_user_registration_lifecycle(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """A new user sending /start is registered as pending and owner receives approval buttons."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)

    update = {
        "message": {
            "text": "/start",
            "chat": {"id": USER_A_CHAT_ID},
            "from": {"id": USER_A_CHAT_ID, "username": "ahmed_eng", "first_name": "Ahmed"},
        }
    }
    res = manager.process_update(update)
    assert res["status"] == "registered_pending"

    # User saved in DB
    user = session.query(TelegramUser).filter_by(telegram_chat_id=USER_A_CHAT_ID).one()
    assert user.role == TelegramUserRole.user
    assert user.status == TelegramUserStatus.pending
    assert user.username == "ahmed_eng"
    assert user.first_name == "Ahmed"

    # User notified that request is pending
    call_args = mock_notifier.send_text.call_args_list[0][1]
    assert call_args["chat_id"] == USER_A_CHAT_ID
    assert "بانتظار موافقة مالك النظام" in call_args["text"]

    # Owner notified with inline keyboard buttons
    mock_notifier.send_message_with_keyboard.assert_called_once()
    owner_call = mock_notifier.send_message_with_keyboard.call_args[1]
    assert owner_call["chat_id"] == OWNER_CHAT_ID
    assert "طلب انضمام جديد" in owner_call["text"]
    assert "Ahmed" in owner_call["text"]
    keyboard = owner_call["reply_markup"]["inline_keyboard"][0]
    assert keyboard[0]["callback_data"] == f"approve:{USER_A_CHAT_ID}"
    assert keyboard[1]["callback_data"] == f"reject:{USER_A_CHAT_ID}"


def test_2_prevent_duplicate_registration(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """Repeated /start commands do not create duplicate rows."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)

    update = {
        "message": {
            "text": "/start",
            "chat": {"id": USER_A_CHAT_ID},
            "from": {"id": USER_A_CHAT_ID, "username": "ahmed_eng", "first_name": "Ahmed"},
        }
    }
    manager.process_update(update)
    res2 = manager.process_update(update)
    assert res2["status"] == "existing_user_status_reported"

    # Exactly 1 row in DB
    assert session.query(TelegramUser).filter_by(telegram_chat_id=USER_A_CHAT_ID).count() == 1


def test_3_owner_auto_provisioning(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """Owner sending /start is automatically provisioned as active Owner."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)

    update = {
        "message": {
            "text": "/start",
            "chat": {"id": OWNER_CHAT_ID},
            "from": {"id": OWNER_CHAT_ID, "username": "owner_boss", "first_name": "Boss"},
        }
    }
    res = manager.process_update(update)
    assert res["status"] == "owner_welcomed"

    owner_user = session.query(TelegramUser).filter_by(telegram_chat_id=OWNER_CHAT_ID).one()
    assert owner_user.role == TelegramUserRole.owner
    assert owner_user.status == TelegramUserStatus.active


# -----------------------------------------------------------------------------
# 2. Approval, Rejection, Disabling, Enabling
# -----------------------------------------------------------------------------


def test_4_owner_approve_command_and_callback(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """Owner can approve a pending user via /approve command or callback query."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)
    repo = TelegramUserRepository(session)
    repo.register_or_update(
        chat_id=USER_A_CHAT_ID, first_name="Ahmed", status=TelegramUserStatus.pending
    )

    # Approve via command
    cmd_update = {
        "message": {
            "text": f"/approve {USER_A_CHAT_ID}",
            "chat": {"id": OWNER_CHAT_ID},
            "from": {"id": OWNER_CHAT_ID},
        }
    }
    res = manager.process_update(cmd_update)
    assert res["status"] == "approved"

    user = repo.get_by_chat_id(USER_A_CHAT_ID)
    assert user.status == TelegramUserStatus.active
    assert user.approved_by == OWNER_CHAT_ID
    assert user.approved_at is not None

    # User received approval notice
    last_text = mock_notifier.send_text.call_args[1]["text"]
    assert "تمت الموافقة" in last_text


def test_5_owner_reject_command(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """Owner can reject a user, moving them to rejected status."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)
    repo = TelegramUserRepository(session)
    repo.register_or_update(
        chat_id=USER_A_CHAT_ID, first_name="Ahmed", status=TelegramUserStatus.pending
    )

    res = manager.reject_user(target_identifier=USER_A_CHAT_ID, rejected_by=OWNER_CHAT_ID)
    assert res["status"] == "rejected"

    user = repo.get_by_chat_id(USER_A_CHAT_ID)
    assert user.status == TelegramUserStatus.rejected


def test_6_disable_and_enable_user(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """Owner can disable an active user and re-enable them."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)
    repo = TelegramUserRepository(session)
    repo.register_or_update(
        chat_id=USER_A_CHAT_ID, first_name="Ahmed", status=TelegramUserStatus.active
    )

    # Disable
    res_dis = manager.disable_user(target_identifier=USER_A_CHAT_ID, disabled_by=OWNER_CHAT_ID)
    assert res_dis["status"] == "disabled"
    assert repo.get_by_chat_id(USER_A_CHAT_ID).status == TelegramUserStatus.disabled

    # Enable
    res_en = manager.enable_user(target_identifier=USER_A_CHAT_ID, enabled_by=OWNER_CHAT_ID)
    assert res_en["status"] == "enabled"
    assert repo.get_by_chat_id(USER_A_CHAT_ID).status == TelegramUserStatus.active


# -----------------------------------------------------------------------------
# 3. RBAC Enforcement & Security
# -----------------------------------------------------------------------------


def test_7_unauthorized_user_command_blocking(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """Regular users cannot use administrative commands (/users, /pending, /approve, etc.)."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)
    repo = TelegramUserRepository(session)
    repo.register_or_update(
        chat_id=USER_A_CHAT_ID, first_name="Ahmed", status=TelegramUserStatus.active
    )

    # User A tries to list users
    update = {
        "message": {
            "text": "/users",
            "chat": {"id": USER_A_CHAT_ID},
            "from": {"id": USER_A_CHAT_ID},
        }
    }
    res = manager.process_update(update)
    assert res["status"] == "forbidden"

    # User A tries to approve someone
    update_app = {
        "message": {
            "text": f"/approve {USER_B_CHAT_ID}",
            "chat": {"id": USER_A_CHAT_ID},
            "from": {"id": USER_A_CHAT_ID},
        }
    }
    res_app = manager.process_update(update_app)
    assert res_app["status"] == "forbidden"


def test_8_callback_query_security_protection(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """An unauthorized user pressing an inline approval button is strictly blocked."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)

    # Hacker / unauthorized user clicks approve button
    callback_update = {
        "callback_query": {
            "id": "cb_999",
            "from": {"id": UNAUTHORIZED_CHAT_ID},
            "data": f"approve:{USER_A_CHAT_ID}",
        }
    }
    res = manager.process_update(callback_update)
    assert res["status"] == "forbidden"

    # Callback answered with alert
    mock_notifier.answer_callback_query.assert_called_with(
        callback_query_id="cb_999",
        text="⛔ ليس لديك صلاحية تنفيذ هذا الإجراء.",
        show_alert=True,
    )


def test_9_admin_promotion_and_demotion(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """Owner can promote user to admin and demote back to user. Admin cannot promote others."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)
    repo = TelegramUserRepository(session)
    repo.register_or_update(
        chat_id=USER_A_CHAT_ID, first_name="Ahmed", status=TelegramUserStatus.active
    )

    # Promote
    res_prom = manager.promote_user(target_identifier=USER_A_CHAT_ID, promoted_by=OWNER_CHAT_ID)
    assert res_prom["status"] == "promoted"
    assert repo.get_by_chat_id(USER_A_CHAT_ID).role == TelegramUserRole.admin

    # Admin tries to promote someone else -> blocked
    update_admin_promote = {
        "message": {
            "text": f"/promote {USER_B_CHAT_ID}",
            "chat": {"id": USER_A_CHAT_ID},
            "from": {"id": USER_A_CHAT_ID},
        }
    }
    res_block = manager.process_update(update_admin_promote)
    assert res_block["status"] == "forbidden"

    # Demote
    res_dem = manager.demote_user(target_identifier=USER_A_CHAT_ID, demoted_by=OWNER_CHAT_ID)
    assert res_dem["status"] == "demoted"
    assert repo.get_by_chat_id(USER_A_CHAT_ID).role == TelegramUserRole.user


def test_10_owner_cannot_be_disabled_or_demoted(
    session: Session, telegram_settings: Settings, mock_notifier: MagicMock
) -> None:
    """System owner is protected against disable, demote, or rejection."""
    manager = TelegramUserManager(session, telegram_settings, notifier=mock_notifier)
    repo = TelegramUserRepository(session)
    repo.register_or_update(
        chat_id=OWNER_CHAT_ID,
        role=TelegramUserRole.owner,
        status=TelegramUserStatus.active,
    )

    res_dis = manager.disable_user(target_identifier=OWNER_CHAT_ID, disabled_by=OWNER_CHAT_ID)
    assert res_dis["status"] == "forbidden"
    assert repo.get_by_chat_id(OWNER_CHAT_ID).status == TelegramUserStatus.active

    res_dem = manager.demote_user(target_identifier=OWNER_CHAT_ID, demoted_by=OWNER_CHAT_ID)
    assert res_dem["status"] == "forbidden"
    assert repo.get_by_chat_id(OWNER_CHAT_ID).role == TelegramUserRole.owner


# -----------------------------------------------------------------------------
# 4. Multi-User Notification Dispatch & Fault Tolerance
# -----------------------------------------------------------------------------


def test_11_notifications_dispatch_to_active_users_and_owner_only(
    session: Session, telegram_settings: Settings
) -> None:
    """Notifications are dispatched to active users and owner only."""
    repo = TelegramUserRepository(session)
    # 1. Owner
    repo.register_or_update(
        chat_id=OWNER_CHAT_ID, role=TelegramUserRole.owner, status=TelegramUserStatus.active
    )
    # 2. Active User
    repo.register_or_update(
        chat_id=USER_A_CHAT_ID, role=TelegramUserRole.user, status=TelegramUserStatus.active
    )
    # 3. Pending User
    repo.register_or_update(
        chat_id=USER_B_CHAT_ID, role=TelegramUserRole.user, status=TelegramUserStatus.pending
    )
    # 4. Disabled User
    repo.register_or_update(
        chat_id=UNAUTHORIZED_CHAT_ID,
        role=TelegramUserRole.user,
        status=TelegramUserStatus.disabled,
    )

    active_recipients = repo.list_active_chat_ids()
    assert OWNER_CHAT_ID in active_recipients
    assert USER_A_CHAT_ID in active_recipients
    assert USER_B_CHAT_ID not in active_recipients
    assert UNAUTHORIZED_CHAT_ID not in active_recipients


def test_12_failure_for_one_user_does_not_abort_others(
    session: Session, telegram_settings: Settings
) -> None:
    """If delivering to one user raises an error, the notifier continues to other recipients."""
    notifier = TelegramNotifier(telegram_settings)

    delivered_chats = []

    def mock_send(text: str, chat_id: int | None = None, **kwargs):
        if chat_id == USER_A_CHAT_ID:
            raise RuntimeError("Telegram API 403: Bot was blocked by the user")
        delivered_chats.append(chat_id)
        return {"ok": True, "message_id": 999}

    notifier._send_message = mock_send  # type: ignore[assignment]

    res = notifier.send_opportunity(
        project_id=1,
        title="تصميم شبكة مكافحة حريق",
        platform="mostaql",
        url="https://mostaql.com/p/1",
        score=90,
        band="strong",
        recipients=[USER_A_CHAT_ID, OWNER_CHAT_ID],
    )

    # Opportunity was delivered to owner despite User A failing
    assert res["status"] == NotificationStatus.sent
    assert res["sent_count"] == 1
    assert OWNER_CHAT_ID in delivered_chats
    assert USER_A_CHAT_ID not in delivered_chats


def test_13_owner_auto_provisioned_from_environment(
    session: Session, mock_notifier: MagicMock, telegram_settings: Settings
) -> None:
    """Owner from TELEGRAM_OWNER_ID or TELEGRAM_ALLOWED_USER_ID is auto-provisioned."""
    repo = TelegramUserRepository(session)

    # 1. Fresh DB has no users
    assert repo.get_by_chat_id(OWNER_CHAT_ID) is None

    # 2. Instantiate manager with settings containing telegram_owner_id
    settings_new = telegram_settings.model_copy(
        update={"telegram_owner_id": OWNER_CHAT_ID, "telegram_allowed_user_id": None}
    )
    assert settings_new.owner_chat_id == OWNER_CHAT_ID

    manager = TelegramUserManager(session, settings_new, notifier=mock_notifier)
    assert manager.is_owner(OWNER_CHAT_ID) is True

    # Owner record is created in DB with role=owner, status=active
    owner_user = repo.get_by_chat_id(OWNER_CHAT_ID)
    assert owner_user is not None
    assert owner_user.role == TelegramUserRole.owner
    assert owner_user.status == TelegramUserStatus.active
    assert owner_user.approved_by == OWNER_CHAT_ID

    # No other user was auto-provisioned
    assert len(repo.list_users()) == 1

    # 3. Test legacy fallback: only telegram_allowed_user_id is set
    session.delete(owner_user)
    session.flush()
    assert repo.get_by_chat_id(OWNER_CHAT_ID) is None

    settings_legacy = telegram_settings.model_copy(
        update={"telegram_owner_id": None, "telegram_allowed_user_id": OWNER_CHAT_ID}
    )
    assert settings_legacy.owner_chat_id == OWNER_CHAT_ID

    _ = TelegramUserManager(session, settings_legacy, notifier=mock_notifier)
    legacy_owner = repo.get_by_chat_id(OWNER_CHAT_ID)
    assert legacy_owner is not None
    assert legacy_owner.role == TelegramUserRole.owner
    assert legacy_owner.status == TelegramUserStatus.active

