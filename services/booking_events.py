"""Side effects of booking lifecycle events: Sheets, admin notices, reminders.

Handlers call these methods after the DB transaction has committed.
Slow or unreliable work (Google API, Telegram fan-out) runs in background
tasks so the user gets an instant answer, and a failure there never
affects the saved booking.
"""

import asyncio
import logging
from collections.abc import Coroutine
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import BookingStatus
from services import booking_service
from services.booking_service import BookingInfo
from services.notifier import Notifier
from services.sheets import SheetsClient

logger = logging.getLogger(__name__)


class ReminderPlanner(Protocol):
    """What BookingEvents needs from the reminder scheduler."""

    def schedule(self, booking_id: int, start_at: datetime) -> None: ...

    def cancel(self, booking_id: int) -> None: ...


class BookingEvents:
    """Fan out booking events to Sheets, notifier and reminders."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        notifier: Notifier,
        reminders: ReminderPlanner,
        sheets: SheetsClient | None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._notifier = notifier
        self._reminders = reminders
        self._sheets = sheets
        self._tasks: set[asyncio.Task[None]] = set()

    def _spawn(self, coro: Coroutine[Any, Any, Any], what: str) -> None:
        """Run a side effect in the background; log, never raise."""

        async def guarded() -> None:
            try:
                await coro
            except Exception:
                logger.exception("Background task failed: %s", what)

        task = asyncio.create_task(guarded(), name=what)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def on_created(self, info: BookingInfo) -> None:
        """New booking: reminder, Sheets row, admin notification."""
        self._reminders.schedule(info.id, info.start_at)
        if self._sheets is not None:
            self._spawn(self._sheets.append_booking(info), f"sheets append #{info.id}")
        self._spawn(self._notifier.booking_created(info), f"notify admins #{info.id}")

    async def on_cancelled(self, info: BookingInfo, by_admin: bool) -> None:
        """Cancelled booking: drop reminder, update Sheets, notify the other side."""
        self._reminders.cancel(info.id)
        if self._sheets is not None:
            self._spawn(
                self._sheets.update_status(info.id, BookingStatus.CANCELLED),
                f"sheets cancel #{info.id}",
            )
        if by_admin:
            self._spawn(self._notifier.booking_cancelled_by_admin(info), f"notify client #{info.id}")
        else:
            self._spawn(self._notifier.booking_cancelled_by_client(info), f"notify admins #{info.id}")

    async def complete_finished(self) -> None:
        """Periodic job: mark past bookings as DONE and mirror it to Sheets."""
        async with self._sessionmaker() as session:
            finished = await booking_service.complete_finished_bookings(session)
        if self._sheets is None:
            return
        for info in finished:
            await self._sheets.update_status(info.id, BookingStatus.DONE)

    async def drain(self) -> None:
        """Wait for background tasks on shutdown."""
        if self._tasks:
            logger.info("Waiting for %d background tasks", len(self._tasks))
            await asyncio.gather(*self._tasks, return_exceptions=True)
