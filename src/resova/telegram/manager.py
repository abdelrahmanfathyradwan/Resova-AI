"""Telegram User Management & Access Control Service.

Implements role-based access control (RBAC):
- Owner: System owner defined in environment settings (full authority).
- Admin: Designated administrator granted specific privileges by Owner.
- User: Regular end-user eligible to receive opportunity notifications.

Handles the full registration lifecycle, interactive approval via inline buttons,
user administrative commands, and callback query security.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from resova.config.settings import Settings
from resova.db.base import utcnow
from resova.db.models import TelegramUser, TelegramUserRole, TelegramUserStatus
from resova.db.repositories import TelegramUserRepository
from resova.telegram.bot import TelegramNotifier

logger = logging.getLogger(__name__)

FORBIDDEN_MSG = "⛔ عذراً، هذا الأمر مخصص لمالك النظام فقط."


class TelegramUserManager:
    """Manages Telegram user registration, authorization, and administrative commands."""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._repo = TelegramUserRepository(session)
        self._notifier = notifier or TelegramNotifier(settings)
        if self.owner_chat_id is not None:
            self._repo.ensure_owner(self.owner_chat_id)

    @property
    def owner_chat_id(self) -> int | None:
        return self._settings.owner_chat_id

    def is_owner(self, chat_id: int) -> bool:
        """Check if chat_id belongs to the trusted system owner."""
        if self.owner_chat_id is not None and chat_id == self.owner_chat_id:
            return True
        user = self._repo.get_by_chat_id(chat_id)
        return user is not None and user.role == TelegramUserRole.owner

    def is_admin(self, chat_id: int) -> bool:
        """Check if chat_id is owner or active admin."""
        if self.is_owner(chat_id):
            return True
        user = self._repo.get_by_chat_id(chat_id)
        return (
            user is not None
            and user.role == TelegramUserRole.admin
            and user.status == TelegramUserStatus.active
        )

    def is_active_user(self, chat_id: int) -> bool:
        """Check if user exists and is active."""
        if self.is_owner(chat_id):
            return True
        user = self._repo.get_by_chat_id(chat_id)
        return user is not None and user.status == TelegramUserStatus.active

    def process_update(self, update: dict[str, Any]) -> dict[str, Any]:
        """Process incoming Telegram Update (Message or CallbackQuery)."""
        if "callback_query" in update:
            return self._handle_callback_query(update["callback_query"])
        if "message" in update:
            return self._handle_message(update["message"])
        return {"status": "ignored", "reason": "unsupported_update_type"}

    # -------------------------------------------------------------------------
    # Message / Command Handling
    # -------------------------------------------------------------------------

    def _handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        chat_id = chat.get("id") or from_user.get("id")
        if not chat_id:
            return {"status": "ignored", "reason": "missing_chat_id"}

        username = from_user.get("username")
        first_name = from_user.get("first_name") or "مستخدم"

        # Auto-provision owner if matching settings
        if self.owner_chat_id is not None and chat_id == self.owner_chat_id:
            self._ensure_owner_provisioned(chat_id, username, first_name)

        if not text.startswith("/"):
            return {"status": "ignored", "reason": "not_a_command"}

        parts = text.split()
        command = parts[0].lower().split("@")[0]  # strip bot username if present
        args = parts[1:]

        handlers = {
            "/start": self._cmd_start,
            "/help": self._cmd_help,
            "/users": self._cmd_users,
            "/pending": self._cmd_pending,
            "/admins": self._cmd_admins,
            "/user": self._cmd_user,
            "/approve": self._cmd_approve,
            "/reject": self._cmd_reject,
            "/disable": self._cmd_disable,
            "/enable": self._cmd_enable,
            "/remove": self._cmd_remove,
            "/promote": self._cmd_promote,
            "/demote": self._cmd_demote,
        }

        handler = handlers.get(command)
        if handler:
            return handler(chat_id, username, first_name, args)

        # Unknown command
        if self.is_active_user(chat_id):
            self._notifier.send_text(
                chat_id=chat_id,
                text="عذراً، أمر غير معروف. استخدم /help لمعرفة الأوامر المتاحة.",
            )
        return {"status": "unknown_command", "command": command}

    def _ensure_owner_provisioned(
        self, chat_id: int, username: str | None, first_name: str | None
    ) -> TelegramUser:
        """Ensure owner has an active owner record in DB."""
        return self._repo.ensure_owner(chat_id, username, first_name)

    def _cmd_start(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Handle /start registration lifecycle."""
        # Case 1: Owner
        if self.is_owner(chat_id):
            msg = (
                f"👑 *مرحباً بك يا مالك النظام ({first_name}) في Resova AI!*\n\n"
                "أنت المالك المخول بإدارة المستخدمين وتلقي إشعارات الفرص المباشرة والملخصات.\n\n"
                "📋 *أوامر الإدارة المتاحة:*\n"
                "• /users — عرض جميع المستخدمين وحالاتهم\n"
                "• /pending — عرض طلبات الانضمام المعلقة\n"
                "• /admins — عرض المشرفين\n"
                "• /user <ID> — تفاصيل مستخدم محدد\n"
                "• /approve <ID> — الموافقة على طلب\n"
                "• /reject <ID> — رفض طلب\n"
                "• /disable <ID> — تعطيل حساب مستخدم\n"
                "• /enable <ID> — إعادة تفعيل حساب\n"
                "• /promote <ID> — ترقية مستخدم إلى Admin\n"
                "• /demote <ID> — خفض رتبة Admin إلى User"
            )
            self._notifier.send_text(chat_id=chat_id, text=msg)
            return {"status": "owner_welcomed", "chat_id": chat_id}

        # Case 2: Other user
        user = self._repo.get_by_chat_id(chat_id)
        if user is not None:
            # Already registered
            if user.status == TelegramUserStatus.active:
                msg = f"👋 أهلاً بك يا {first_name}! حسابك نشط وتصلك الإشعارات فور رصدها."
            elif user.status == TelegramUserStatus.pending:
                msg = "⏳ طلب انضمامك قيد المراجعة حالياً من قبل مالك النظام."
            elif user.status == TelegramUserStatus.rejected:
                msg = "❌ عذراً، تم رفض طلب انضمامك إلى البوت من قبل مالك النظام."
            elif user.status == TelegramUserStatus.disabled:
                msg = "⚠️ عذراً، حسابك معطل حالياً من قبل الإدارة."
            else:
                msg = "حالة حسابك غير معروفة. يرجى التواصل مع الإدارة."
            self._notifier.send_text(chat_id=chat_id, text=msg)
            return {"status": "existing_user_status_reported", "user_status": user.status.value}

        # New user registration
        user, _ = self._repo.register_or_update(
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            role=TelegramUserRole.user,
            status=TelegramUserStatus.pending,
        )

        # Notify user
        user_msg = (
            f"👋 مرحباً {first_name}!\n\n"
            "تم استلام طلب انضمامك إلى منصة *Resova AI* بنجاح.\n"
            "طلبك الآن بانتظار موافقة مالك النظام. سيصلك إشعار فوري عند تفعيل حسابك."
        )
        self._notifier.send_text(chat_id=chat_id, text=user_msg)

        # Notify Owner with Interactive Buttons
        if self.owner_chat_id:
            u_tag = f"@{username}" if username else "غير محدد"
            owner_msg = (
                "🔔 *طلب انضمام جديد للبوت!*\n\n"
                f"👤 *الاسم:* {first_name}\n"
                f"🏷️ *اسم المستخدم:* {u_tag}\n"
                f"🆔 *معرف المحادثة (Chat ID):* `{chat_id}`\n\n"
                "هل توافق على منح هذا المستخدم صلاحية استقبال الإشعارات؟"
            )
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "✅ موافقة (Approve)", "callback_data": f"approve:{chat_id}"},
                        {"text": "❌ رفض (Reject)", "callback_data": f"reject:{chat_id}"},
                    ]
                ]
            }
            self._notifier.send_message_with_keyboard(
                chat_id=self.owner_chat_id,
                text=owner_msg,
                reply_markup=keyboard,
            )

        return {"status": "registered_pending", "chat_id": chat_id}

    def _cmd_help(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        if self.is_owner(chat_id):
            return self._cmd_start(chat_id, username, first_name, args)
        if self.is_active_user(chat_id):
            msg = (
                "🤖 *أوامر Resova AI المتاحة:*\n\n"
                "• /start — التحقق من حالة حسابك\n"
                "• /help — عرض هذه المساعدة\n"
                "• /admins — عرض مسؤولي النظام"
            )
            self._notifier.send_text(chat_id=chat_id, text=msg)
            return {"status": "help_sent"}
        return {"status": "unauthorized"}

    def _cmd_users(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """List all users (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(chat_id=chat_id, text=FORBIDDEN_MSG)
            return {"status": "forbidden"}

        users = self._repo.list_users()
        if not users:
            self._notifier.send_text(chat_id=chat_id, text="لا يوجد مستخدمون مسجلون حالياً.")
            return {"status": "empty"}

        lines = ["👥 *قائمة مستخدمي Resova AI:*", ""]
        role_badges = {
            TelegramUserRole.owner: "👑 مالك",
            TelegramUserRole.admin: "🛡️ مشرف",
            TelegramUserRole.user: "👤 مستخدم",
        }
        status_badges = {
            TelegramUserStatus.active: "🟢 نشط",
            TelegramUserStatus.pending: "🟡 معلق",
            TelegramUserStatus.rejected: "🔴 مرفوض",
            TelegramUserStatus.disabled: "⚪ معطل",
        }

        for u in users:
            r_badge = role_badges.get(u.role, u.role.value)
            s_badge = status_badges.get(u.status, u.status.value)
            u_tag = f"@{u.username}" if u.username else "بدون يوزر"
            name = u.first_name or "بدون اسم"
            lines.append(
                f"• `{u.telegram_chat_id}` | *{name}* ({u_tag})\n"
                f"   الرتبة: {r_badge} | الحالة: {s_badge}"
            )

        self._notifier.send_text(chat_id=chat_id, text="\n".join(lines))
        return {"status": "users_listed", "count": len(users)}

    def _cmd_pending(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """List pending requests with quick approve/reject buttons (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(chat_id=chat_id, text=FORBIDDEN_MSG)
            return {"status": "forbidden"}

        pending_users = self._repo.list_users(status=TelegramUserStatus.pending)
        if not pending_users:
            self._notifier.send_text(chat_id=chat_id, text="✨ لا توجد أي طلبات انضمام معلقة.")
            return {"status": "empty"}

        self._notifier.send_text(
            chat_id=chat_id,
            text=f"📋 يوجد {len(pending_users)} طلب(ات) انضمام معلقة:",
        )

        for u in pending_users:
            u_tag = f"@{u.username}" if u.username else "بدون يوزر"
            text = (
                f"👤 *طلب انضمام:*\n"
                f"• الاسم: {u.first_name or 'بدون اسم'}\n"
                f"• المستخدم: {u_tag}\n"
                f"• المعرف: `{u.telegram_chat_id}`"
            )
            keyboard = {
                "inline_keyboard": [
                    [
                        {"text": "✅ قبول", "callback_data": f"approve:{u.telegram_chat_id}"},
                        {"text": "❌ رفض", "callback_data": f"reject:{u.telegram_chat_id}"},
                    ]
                ]
            }
            self._notifier.send_message_with_keyboard(
                chat_id=chat_id, text=text, reply_markup=keyboard
            )

        return {"status": "pending_listed", "count": len(pending_users)}

    def _cmd_admins(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """List admins and owner."""
        if not (self.is_owner(chat_id) or self.is_admin(chat_id)):
            self._notifier.send_text(chat_id=chat_id, text="⛔ ليس لديك صلاحية عرض هذه القائمة.")
            return {"status": "forbidden"}

        admins = self._repo.list_users(role=TelegramUserRole.admin)
        owners = self._repo.list_users(role=TelegramUserRole.owner)

        lines = ["🛡️ *قائمة مسؤولي النظام:*", ""]
        if owners:
            lines.append("*المالك (Owner):*")
            for o in owners:
                u_tag = f"@{o.username}" if o.username else ""
                lines.append(f"• 👑 {o.first_name} ({u_tag}) — `{o.telegram_chat_id}`")
            lines.append("")

        lines.append("*المشرفون (Admins):*")
        if admins:
            for a in admins:
                u_tag = f"@{a.username}" if a.username else ""
                status_str = "🟢 نشط" if a.status == TelegramUserStatus.active else "⚪ معطل"
                lines.append(
                    f"• 🛡️ {a.first_name} ({u_tag}) — `{a.telegram_chat_id}` [{status_str}]"
                )
        else:
            lines.append("لا يوجد مشرفون معينون حالياً.")

        self._notifier.send_text(chat_id=chat_id, text="\n".join(lines))
        return {"status": "admins_listed"}

    def _cmd_user(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Display details of a specific user (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(chat_id=chat_id, text=FORBIDDEN_MSG)
            return {"status": "forbidden"}
        if not args:
            self._notifier.send_text(chat_id=chat_id, text="يرجى تحديد المعرف: `/user <ID>`")
            return {"status": "missing_args"}

        user = self._repo.get_by_identifier(args[0])
        if user is None:
            msg = f"لم يتم العثور على مستخدم بالمعرف `{args[0]}`."
            self._notifier.send_text(chat_id=chat_id, text=msg)
            return {"status": "not_found"}

        u_tag = f"@{user.username}" if user.username else "بدون يوزر"
        reg_time = user.registered_at.strftime("%Y-%m-%d %H:%M UTC")
        app_time = (
            user.approved_at.strftime("%Y-%m-%d %H:%M UTC")
            if user.approved_at
            else "لم يتم بعد"
        )

        text = (
            "👤 *بطاقة تفاصيل المستخدم:*\n\n"
            f"• *الرقم التعريفي (DB ID):* `{user.id}`\n"
            f"• *المعرف (Chat ID):* `{user.telegram_chat_id}`\n"
            f"• *الاسم:* {user.first_name or 'غير محدد'}\n"
            f"• *اسم المستخدم:* {u_tag}\n"
            f"• *الرتبة:* `{user.role.value}`\n"
            f"• *الحالة:* `{user.status.value}`\n"
            f"• *تاريخ التسجيل:* {reg_time}\n"
            f"• *تاريخ الموافقة:* {app_time}\n"
            f"• *الموافِق:* `{user.approved_by or 'النظام'}`"
        )

        buttons = []
        if user.role != TelegramUserRole.owner:
            if user.status == TelegramUserStatus.active:
                buttons.append({
                    "text": "⚠️ تعطيل",
                    "callback_data": f"disable:{user.telegram_chat_id}",
                })
            else:
                buttons.append({
                    "text": "✅ تفعيل",
                    "callback_data": f"enable:{user.telegram_chat_id}",
                })

            if user.role == TelegramUserRole.user:
                buttons.append({
                    "text": "🛡️ ترقية إلى Admin",
                    "callback_data": f"promote:{user.telegram_chat_id}",
                })
            elif user.role == TelegramUserRole.admin:
                buttons.append({
                    "text": "👤 خفض إلى User",
                    "callback_data": f"demote:{user.telegram_chat_id}",
                })

        keyboard = {"inline_keyboard": [buttons]} if buttons else None
        if keyboard:
            self._notifier.send_message_with_keyboard(
                chat_id=chat_id, text=text, reply_markup=keyboard
            )
        else:
            self._notifier.send_text(chat_id=chat_id, text=text)

        return {"status": "user_shown", "user_id": user.id}

    def _cmd_approve(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Approve a user (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(chat_id=chat_id, text=FORBIDDEN_MSG)
            return {"status": "forbidden"}
        if not args:
            self._notifier.send_text(chat_id=chat_id, text="يرجى تحديد المعرف: `/approve <ID>`")
            return {"status": "missing_args"}

        return self.approve_user(target_identifier=args[0], approved_by=chat_id)

    def _cmd_reject(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Reject a user (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(chat_id=chat_id, text=FORBIDDEN_MSG)
            return {"status": "forbidden"}
        if not args:
            self._notifier.send_text(chat_id=chat_id, text="يرجى تحديد المعرف: `/reject <ID>`")
            return {"status": "missing_args"}

        return self.reject_user(target_identifier=args[0], rejected_by=chat_id)

    def _cmd_disable(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Disable a user (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(chat_id=chat_id, text=FORBIDDEN_MSG)
            return {"status": "forbidden"}
        if not args:
            self._notifier.send_text(chat_id=chat_id, text="يرجى تحديد المعرف: `/disable <ID>`")
            return {"status": "missing_args"}

        return self.disable_user(target_identifier=args[0], disabled_by=chat_id)

    def _cmd_enable(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Enable a user (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(chat_id=chat_id, text=FORBIDDEN_MSG)
            return {"status": "forbidden"}
        if not args:
            self._notifier.send_text(chat_id=chat_id, text="يرجى تحديد المعرف: `/enable <ID>`")
            return {"status": "missing_args"}

        return self.enable_user(target_identifier=args[0], enabled_by=chat_id)

    def _cmd_remove(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Remove user access (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(chat_id=chat_id, text=FORBIDDEN_MSG)
            return {"status": "forbidden"}
        if not args:
            self._notifier.send_text(chat_id=chat_id, text="يرجى تحديد المعرف: `/remove <ID>`")
            return {"status": "missing_args"}

        return self.remove_user(target_identifier=args[0], removed_by=chat_id)

    def _cmd_promote(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Promote user to Admin (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(
                chat_id=chat_id,
                text="⛔ عذراً، ترقية المشرفين صلاحية حصرية لمالك النظام.",
            )
            return {"status": "forbidden"}
        if not args:
            self._notifier.send_text(chat_id=chat_id, text="يرجى تحديد المعرف: `/promote <ID>`")
            return {"status": "missing_args"}

        return self.promote_user(target_identifier=args[0], promoted_by=chat_id)

    def _cmd_demote(
        self, chat_id: int, username: str | None, first_name: str, args: list[str]
    ) -> dict[str, Any]:
        """Demote Admin to User (Owner only)."""
        if not self.is_owner(chat_id):
            self._notifier.send_text(
                chat_id=chat_id,
                text="⛔ عذراً، خفض رتبة المشرفين صلاحية حصرية لمالك النظام.",
            )
            return {"status": "forbidden"}
        if not args:
            self._notifier.send_text(chat_id=chat_id, text="يرجى تحديد المعرف: `/demote <ID>`")
            return {"status": "missing_args"}

        return self.demote_user(target_identifier=args[0], demoted_by=chat_id)

    # -------------------------------------------------------------------------
    # Administrative Actions (Reusable by Commands & Callbacks)
    # -------------------------------------------------------------------------

    def approve_user(self, target_identifier: int | str, approved_by: int) -> dict[str, Any]:
        user = self._repo.get_by_identifier(target_identifier)
        if user is None:
            msg = f"لم يتم العثور على مستخدم بالمعرف `{target_identifier}`."
            self._notifier.send_text(chat_id=approved_by, text=msg)
            return {"status": "not_found"}

        user.status = TelegramUserStatus.active
        user.approved_at = utcnow()
        user.approved_by = approved_by
        self._session.flush()

        self._notifier.send_text(
            chat_id=approved_by,
            text=f"✅ تمت الموافقة على انضمام {user.first_name} (`{user.telegram_chat_id}`).",
        )
        self._notifier.send_text(
            chat_id=user.telegram_chat_id,
            text="🎉 *تهانينا!*\nتمت الموافقة على حسابك في Resova AI. ستصلك الآن إشعارات الفرص.",
        )
        return {"status": "approved", "chat_id": user.telegram_chat_id}

    def reject_user(self, target_identifier: int | str, rejected_by: int) -> dict[str, Any]:
        user = self._repo.get_by_identifier(target_identifier)
        if user is None:
            msg = f"لم يتم العثور على مستخدم بالمعرف `{target_identifier}`."
            self._notifier.send_text(chat_id=rejected_by, text=msg)
            return {"status": "not_found"}
        if user.role == TelegramUserRole.owner:
            self._notifier.send_text(chat_id=rejected_by, text="⛔ لا يمكن رفض أو استبعاد المالك.")
            return {"status": "forbidden"}

        user.status = TelegramUserStatus.rejected
        self._session.flush()

        self._notifier.send_text(
            chat_id=rejected_by,
            text=f"❌ تم رفض طلب انضمام {user.first_name} (`{user.telegram_chat_id}`).",
        )
        self._notifier.send_text(
            chat_id=user.telegram_chat_id,
            text="عذراً، تم رفض طلب انضمامك إلى منصة Resova AI.",
        )
        return {"status": "rejected", "chat_id": user.telegram_chat_id}

    def disable_user(self, target_identifier: int | str, disabled_by: int) -> dict[str, Any]:
        user = self._repo.get_by_identifier(target_identifier)
        if user is None:
            msg = f"لم يتم العثور على مستخدم بالمعرف `{target_identifier}`."
            self._notifier.send_text(chat_id=disabled_by, text=msg)
            return {"status": "not_found"}
        if user.role == TelegramUserRole.owner:
            self._notifier.send_text(chat_id=disabled_by, text="⛔ لا يمكن تعطيل مالك النظام.")
            return {"status": "forbidden"}

        user.status = TelegramUserStatus.disabled
        self._session.flush()

        self._notifier.send_text(
            chat_id=disabled_by,
            text=f"⚠️ تم تعطيل حساب {user.first_name} (`{user.telegram_chat_id}`).",
        )
        self._notifier.send_text(
            chat_id=user.telegram_chat_id,
            text="⚠️ تم تعطيل حسابك مؤقتاً في Resova AI من قبل الإدارة.",
        )
        return {"status": "disabled", "chat_id": user.telegram_chat_id}

    def enable_user(self, target_identifier: int | str, enabled_by: int) -> dict[str, Any]:
        user = self._repo.get_by_identifier(target_identifier)
        if user is None:
            msg = f"لم يتم العثور على مستخدم بالمعرف `{target_identifier}`."
            self._notifier.send_text(chat_id=enabled_by, text=msg)
            return {"status": "not_found"}

        user.status = TelegramUserStatus.active
        self._session.flush()

        self._notifier.send_text(
            chat_id=enabled_by,
            text=f"✅ تمت إعادة تفعيل حساب {user.first_name} (`{user.telegram_chat_id}`).",
        )
        self._notifier.send_text(
            chat_id=user.telegram_chat_id,
            text="✅ تمت إعادة تفعيل حسابك في Resova AI. ستصلك الإشعارات كالمعتاد.",
        )
        return {"status": "enabled", "chat_id": user.telegram_chat_id}

    def remove_user(self, target_identifier: int | str, removed_by: int) -> dict[str, Any]:
        user = self._repo.get_by_identifier(target_identifier)
        if user is None:
            msg = f"لم يتم العثور على مستخدم بالمعرف `{target_identifier}`."
            self._notifier.send_text(chat_id=removed_by, text=msg)
            return {"status": "not_found"}
        if user.role == TelegramUserRole.owner:
            self._notifier.send_text(chat_id=removed_by, text="⛔ لا يمكن إزالة مالك النظام.")
            return {"status": "forbidden"}

        user.status = TelegramUserStatus.disabled
        self._session.flush()

        self._notifier.send_text(
            chat_id=removed_by,
            text=f"🗑️ تم إلغاء وصول المستخدم {user.first_name} (`{user.telegram_chat_id}`).",
        )
        return {"status": "removed", "chat_id": user.telegram_chat_id}

    def promote_user(self, target_identifier: int | str, promoted_by: int) -> dict[str, Any]:
        user = self._repo.get_by_identifier(target_identifier)
        if user is None:
            msg = f"لم يتم العثور على مستخدم بالمعرف `{target_identifier}`."
            self._notifier.send_text(chat_id=promoted_by, text=msg)
            return {"status": "not_found"}
        if user.role == TelegramUserRole.owner:
            self._notifier.send_text(chat_id=promoted_by, text="المستخدم هو مالك النظام بالفعل.")
            return {"status": "is_owner"}

        user.role = TelegramUserRole.admin
        self._session.flush()

        self._notifier.send_text(
            chat_id=promoted_by,
            text=f"🛡️ تمت ترقية {user.first_name} (`{user.telegram_chat_id}`) إلى مسؤول (Admin).",
        )
        self._notifier.send_text(
            chat_id=user.telegram_chat_id,
            text="🛡️ تمت ترقية حسابك إلى مسؤول (Admin) في منصة Resova AI.",
        )
        return {"status": "promoted", "chat_id": user.telegram_chat_id}

    def demote_user(self, target_identifier: int | str, demoted_by: int) -> dict[str, Any]:
        user = self._repo.get_by_identifier(target_identifier)
        if user is None:
            msg = f"لم يتم العثور على مستخدم بالمعرف `{target_identifier}`."
            self._notifier.send_text(chat_id=demoted_by, text=msg)
            return {"status": "not_found"}
        if user.role == TelegramUserRole.owner:
            self._notifier.send_text(chat_id=demoted_by, text="⛔ لا يمكن خفض رتبة مالك النظام.")
            return {"status": "forbidden"}

        user.role = TelegramUserRole.user
        self._session.flush()

        self._notifier.send_text(
            chat_id=demoted_by,
            text=f"👤 تم خفض رتبة {user.first_name} (`{user.telegram_chat_id}`) إلى User.",
        )
        self._notifier.send_text(
            chat_id=user.telegram_chat_id,
            text="تم تعديل رتبة حسابك إلى مستخدم عادي (User).",
        )
        return {"status": "demoted", "chat_id": user.telegram_chat_id}

    # -------------------------------------------------------------------------
    # Callback Query Security & Handling
    # -------------------------------------------------------------------------

    def _handle_callback_query(self, cb: dict[str, Any]) -> dict[str, Any]:
        cb_id = cb.get("id")
        from_user = cb.get("from") or {}
        caller_chat_id = from_user.get("id")
        data = (cb.get("data") or "").strip()

        if not caller_chat_id:
            return {"status": "ignored", "reason": "missing_caller_id"}

        # STRICT SECURITY CHECK: Only Owner can trigger administrative inline buttons
        if not self.is_owner(caller_chat_id):
            logger.warning(
                "Unauthorized callback query attempt by user %d with data: %s",
                caller_chat_id,
                data,
            )
            if cb_id:
                self._notifier.answer_callback_query(
                    callback_query_id=cb_id,
                    text="⛔ ليس لديك صلاحية تنفيذ هذا الإجراء.",
                    show_alert=True,
                )
            return {"status": "forbidden", "caller_id": caller_chat_id}

        # Acknowledge callback immediately
        if cb_id:
            self._notifier.answer_callback_query(
                callback_query_id=cb_id,
                text="تم استلام الإجراء...",
                show_alert=False,
            )

        if ":" not in data:
            return {"status": "invalid_callback_data"}

        action, target_str = data.split(":", 1)
        if not target_str.isdigit():
            return {"status": "invalid_target_id"}
        target_chat_id = int(target_str)

        actions = {
            "approve": self.approve_user,
            "reject": self.reject_user,
            "disable": self.disable_user,
            "enable": self.enable_user,
            "promote": self.promote_user,
            "demote": self.demote_user,
        }

        func = actions.get(action)
        if func:
            var_name = func.__code__.co_varnames[2]
            return func(target_identifier=target_chat_id, **{var_name: caller_chat_id})

        return {"status": "unknown_action", "action": action}
