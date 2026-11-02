"""aiogram-based implementation of `services.notifier.SendFunc`."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError

logger = logging.getLogger(__name__)


class TelegramSender:
    """Send an HTML message; never raise, return delivery status."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def __call__(self, chat_id: int, text: str) -> bool:
        try:
            await self._bot.send_message(chat_id, text)
        except TelegramForbiddenError:
            logger.info("User %s blocked the bot, message not delivered", chat_id)
            return False
        except TelegramAPIError as exc:
            logger.warning("Failed to send message to %s: %s", chat_id, exc)
            return False
        return True
