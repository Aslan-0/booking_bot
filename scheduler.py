"""Reminder scheduling with APScheduler (in-memory job store).

Jobs are not persisted: on startup `restore()` rebuilds them from the DB,
which is the single source of truth (`reminder_sent` flag).
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services import booking_service
from services.notifier import Notifier

logger = logging.getLogger(__name__)

MISFIRE_GRACE_SEC = 60 * 60
OVERDUE_DELAY = timedelta(seconds=5)


def _job_id(booking_id: int) -> str:
    return f"reminder:{booking_id}"


class ReminderScheduler:
    """Schedule, cancel and restore client reminders."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        notifier: Notifier,
        remind_before: timedelta,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._notifier = notifier
        self._remind_before = remind_before
        self._scheduler = AsyncIOScheduler(timezone=timezone.utc)

    def start(self) -> None:
        """Start the scheduler (must be called inside a running event loop)."""
        self._scheduler.start()
        logger.info("Scheduler started")

    def shutdown(self) -> None:
        """Stop the scheduler without waiting for running jobs."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def add_periodic(
        self, func: Callable[[], Awaitable[None]], minutes: int, job_id: str
    ) -> None:
        """Run a coroutine function every `minutes` minutes."""
        self._scheduler.add_job(
            func, "interval", minutes=minutes, id=job_id,
            replace_existing=True, coalesce=True, max_instances=1,
            next_run_time=datetime.now(timezone.utc),
        )

    def schedule(self, booking_id: int, start_at: datetime) -> None:
        """Plan a reminder `remind_before` ahead of `start_at` (or ASAP if overdue)."""
        now = datetime.now(timezone.utc)
        if start_at <= now:
            return
        run_at = max(start_at - self._remind_before, now + OVERDUE_DELAY)
        self._scheduler.add_job(
            self._send_reminder,
            "date",
            run_date=run_at,
            args=[booking_id],
            id=_job_id(booking_id),
            replace_existing=True,
            misfire_grace_time=MISFIRE_GRACE_SEC,
        )
        logger.info("Reminder for booking %s scheduled at %s", booking_id, run_at.isoformat())

    def cancel(self, booking_id: int) -> None:
        """Remove a scheduled reminder if it exists."""
        try:
            self._scheduler.remove_job(_job_id(booking_id))
            logger.info("Reminder for booking %s removed", booking_id)
        except JobLookupError:
            pass

    async def restore(self) -> int:
        """Re-create reminders for all pending bookings after a restart."""
        async with self._sessionmaker() as session:
            pending = await booking_service.list_pending_reminders(session)
        for info in pending:
            self.schedule(info.id, info.start_at)
        logger.info("Restored %d reminders from DB", len(pending))
        return len(pending)

    async def _send_reminder(self, booking_id: int) -> None:
        async with self._sessionmaker() as session:
            info = await booking_service.claim_reminder(session, booking_id)
        if info is None:
            logger.info("Reminder for booking %s skipped (cancelled, sent or past)", booking_id)
            return
        delivered = await self._notifier.reminder(info)
        logger.info("Reminder for booking %s delivered=%s", booking_id, delivered)
