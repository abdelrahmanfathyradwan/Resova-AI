"""Telegram bot integration (Phase 5).

Delivers scored opportunities to authorised users, provides inline buttons
for interaction, supports multi-user access control and administrative commands.
Never submits proposals or contacts clients.
"""

from resova.telegram.bot import TelegramNotifier
from resova.telegram.manager import TelegramUserManager

__all__ = ["TelegramNotifier", "TelegramUserManager"]
