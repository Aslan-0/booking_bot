"""Texts and delivery of notifications to clients and admins.

The notifier does not depend on aiogram: it receives a `SendFunc`
(chat_id, html_text) -> bool, which is implemented in utils/telegram_sender.py.
"""

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable, Sequence
from zoneinfo import ZoneInfo

from services.booking_service import BookingInfo
from utils.formatting import format_date, format_price, format_time

logger = logging.getLogger(__name__)

SendFunc = Callable[[int, str], Awaitable[bool]]


class Notifier:
    """Build notification texts and deliver them."""

    def __init__(self, send: SendFunc, admin_ids: Sequence[int], tz: ZoneInfo) -> None:
        self._send = send
        self._admin_ids = tuple(admin_ids)
        self._tz = tz

    def _details(self, info: BookingInfo) -> str:
        return (
            f"{html.escape(info.service_title)}\n"
            f"{format_date(info.start_at, self._tz)}\n"
            f"{format_time(info.start_at, self._tz)}–{format_time(info.end_at, self._tz)}\n"
            f"{format_price(info.price)}"
        )

    def _client_line(self, info: BookingInfo) -> str:
        name = html.escape(info.client_name)
        return f'<a href="tg://user?id={info.client_telegram_id}">{name}</a>'

    async def _to_admins(self, text: str) -> None:
        if not self._admin_ids:
            logger.warning("ADMIN_IDS is empty, admin notification skipped")
            return
        await asyncio.gather(*(self._send(admin_id, text) for admin_id in self._admin_ids))

    async def booking_created(self, info: BookingInfo) -> None:
        """Tell admins about a new booking."""
        await self._to_admins(
            f"<b>New booking #{info.id}</b>\n\n{self._client_line(info)}\n{self._details(info)}"
        )

    async def booking_cancelled_by_client(self, info: BookingInfo) -> None:
        """Tell admins that a client cancelled."""
        await self._to_admins(
            f"<b>Client cancelled booking #{info.id}</b>\n\n"
            f"{self._client_line(info)}\n{self._details(info)}"
        )

    async def booking_cancelled_by_admin(self, info: BookingInfo) -> None:
        """Tell the client that the admin cancelled the booking."""
        await self._send(
            info.client_telegram_id,
            "<b>Your booking was cancelled by the administrator</b>\n\n"
            f"{self._details(info)}\n\nBook another time via /start",
        )

    async def reminder(self, info: BookingInfo) -> bool:
        """Remind the client about an upcoming visit."""
        return await self._send(
            info.client_telegram_id,
            f"<b>Booking reminder</b>\n\n{self._details(info)}\n\nSee you soon!",
        )
