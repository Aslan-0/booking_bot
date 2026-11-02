"""Anti-spam middleware: drop updates that come from one user too often."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, TelegramObject, User

logger = logging.getLogger(__name__)

MAX_TRACKED_USERS = 10_000


class ThrottleMiddleware(BaseMiddleware):
    """Allow at most one update per `rate_sec` seconds per user."""

    def __init__(self, rate_sec: float) -> None:
        self._rate = rate_sec
        self._last_seen: dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        last = self._last_seen.get(user.id)
        if last is not None and now - last < self._rate:
            logger.debug("Throttled update from user %s", user.id)
            if isinstance(event, CallbackQuery):
                try:
                    await event.answer("Not so fast")
                except TelegramAPIError as exc:
                    logger.debug("Failed to answer throttled callback: %s", exc)
            return None

        self._last_seen[user.id] = now
        if len(self._last_seen) > MAX_TRACKED_USERS:
            self._cleanup(now)
        return await handler(event, data)

    def _cleanup(self, now: float) -> None:
        """Forget users that were not seen for longer than the rate window."""
        self._last_seen = {
            uid: ts for uid, ts in self._last_seen.items() if now - ts < self._rate
        }
