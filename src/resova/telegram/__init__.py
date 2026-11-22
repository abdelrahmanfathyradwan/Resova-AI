"""Telegram bot integration (Phase 5).

Delivers scored opportunities to a single authorised user, provides inline buttons
for interaction, supports manual project paste, and shows proposal drafts on request.
Never submits proposals or contacts clients.
"""

from resova.telegram.bot import TelegramNotifier

__all__ = ["TelegramNotifier"]
