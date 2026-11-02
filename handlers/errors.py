"""Global error handler: log everything, never let the bot crash."""

import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)
router = Router(name="errors")

USER_ERROR_TEXT = "Something went wrong. Please try again or press /start"


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Log the exception and politely inform the user if possible."""
    logger.exception(
        "Unhandled error while processing update %s",
        event.update.update_id,
        exc_info=event.exception,
    )
    try:
        if event.update.callback_query is not None:
            await event.update.callback_query.answer(USER_ERROR_TEXT, show_alert=True)
        elif event.update.message is not None:
            await event.update.message.answer(USER_ERROR_TEXT)
    except TelegramAPIError as exc:
        logger.warning("Failed to notify user about error: %s", exc)
    return True
