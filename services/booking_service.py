"""Booking business logic. Knows nothing about aiogram."""

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db import repo
from db.models import Booking, BookingStatus, Service
from db.session import SQLITE_BEGIN_MODE
from services.slots import (
    Interval,
    WorkHours,
    booking_dates,
    free_slots,
    working_window,
)

logger = logging.getLogger(__name__)


class BookingError(Exception):
    """Base class for booking errors shown to the user."""


class ServiceNotFoundError(BookingError):
    """Service does not exist or is inactive."""


class SlotUnavailableError(BookingError):
    """Requested slot is already taken or outside working hours."""


class BookingNotFoundError(BookingError):
    """Booking does not exist, belongs to another user or is no longer active."""


class BookingNotCancellableError(BookingError):
    """Booking has already started and cannot be cancelled."""


@dataclass(frozen=True, slots=True)
class ServiceInfo:
    """Read-only service data for the presentation layer."""

    id: int
    title: str
    duration_min: int
    price: int


@dataclass(frozen=True, slots=True)
class BookingInfo:
    """Read-only booking data for the presentation layer (times in UTC)."""

    id: int
    service_title: str
    price: int
    start_at: datetime
    end_at: datetime
    client_name: str
    client_telegram_id: int
    status: BookingStatus
    created_at: datetime


def work_hours() -> WorkHours:
    """Build working hours from settings."""
    return WorkHours(settings.work_start, settings.work_end, settings.slot_step_min)


def utcnow() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def local_day(offset_days: int = 0, now: datetime | None = None) -> date:
    """Today (+offset) in the business time zone."""
    return booking_dates(now or utcnow(), settings.tz, offset_days + 1)[offset_days]


def _service_info(service: Service) -> ServiceInfo:
    return ServiceInfo(service.id, service.title, service.duration_min, service.price)


def to_booking_info(booking: Booking) -> BookingInfo:
    """Convert an ORM booking (with user and service loaded) to a DTO."""
    return BookingInfo(
        id=booking.id,
        service_title=booking.service.title,
        price=booking.service.price,
        start_at=booking.start_at,
        end_at=booking.end_at,
        client_name=booking.user.name,
        client_telegram_id=booking.user.telegram_id,
        status=booking.status,
        created_at=booking.created_at,
    )


async def _busy_intervals(session: AsyncSession, start: datetime, end: datetime) -> list[Interval]:
    """Return active bookings intersecting [start, end) as intervals."""
    bookings = await repo.get_active_bookings_between(session, start, end)
    return [Interval(b.start_at, b.end_at) for b in bookings]


async def _get_active_service(session: AsyncSession, service_id: int) -> Service:
    service = await repo.get_service(session, service_id)
    if service is None or not service.is_active:
        raise ServiceNotFoundError(f"Service {service_id} not found")
    return service


async def list_services(session: AsyncSession) -> list[ServiceInfo]:
    """Return all active services."""
    return [_service_info(s) for s in await repo.list_active_services(session)]


async def get_service_info(session: AsyncSession, service_id: int) -> ServiceInfo:
    """Return an active service or raise ServiceNotFoundError."""
    return _service_info(await _get_active_service(session, service_id))


async def get_dates_with_free_slots(
    session: AsyncSession, service_id: int, now: datetime | None = None
) -> list[date]:
    """Return dates in the booking horizon that have at least one free slot."""
    now = now or utcnow()
    service = await _get_active_service(session, service_id)
    tz, hours = settings.tz, work_hours()
    dates = booking_dates(now, tz, settings.booking_days_ahead)
    range_start = working_window(dates[0], hours, tz).start
    range_end = working_window(dates[-1], hours, tz).end
    busy = await _busy_intervals(session, range_start, range_end)
    return [d for d in dates if free_slots(d, service.duration_min, busy, hours, tz, now)]


async def get_free_slots(
    session: AsyncSession, service_id: int, day: date, now: datetime | None = None
) -> list[datetime]:
    """Return free UTC slot starts for a service on a local date."""
    now = now or utcnow()
    service = await _get_active_service(session, service_id)
    return await _free_slots_for(session, service, day, now)


async def _free_slots_for(
    session: AsyncSession, service: Service, day: date, now: datetime
) -> list[datetime]:
    tz, hours = settings.tz, work_hours()
    window = working_window(day, hours, tz)
    busy = await _busy_intervals(session, window.start, window.end)
    return free_slots(day, service.duration_min, busy, hours, tz, now)


async def create_booking(
    session: AsyncSession,
    telegram_id: int,
    user_name: str,
    service_id: int,
    start_at: datetime,
    now: datetime | None = None,
) -> BookingInfo:
    """Create a booking atomically.

    The availability check and the INSERT run in one `BEGIN IMMEDIATE`
    transaction, so SQLite serialises concurrent writers and two clients
    cannot take the same slot.
    """
    now = now or utcnow()
    start_at = start_at.astimezone(timezone.utc)
    await session.commit()

    async with session.begin():
        await session.connection(execution_options={SQLITE_BEGIN_MODE: "IMMEDIATE"})
        service = await _get_active_service(session, service_id)
        day = start_at.astimezone(settings.tz).date()
        if start_at not in await _free_slots_for(session, service, day, now):
            raise SlotUnavailableError(f"Slot {start_at.isoformat()} is not available")

        user = await repo.get_user_by_telegram_id(session, telegram_id)
        if user is None:
            user = await repo.add_user(session, telegram_id, user_name)
        end_at = start_at + timedelta(minutes=service.duration_min)
        too_late_for_reminder = start_at - timedelta(minutes=settings.reminder_before_min) <= now
        booking = await repo.add_booking(
            session, user.id, service.id, start_at, end_at, reminder_sent=too_late_for_reminder
        )
        info = BookingInfo(
            id=booking.id,
            service_title=service.title,
            price=service.price,
            start_at=start_at,
            end_at=end_at,
            client_name=user.name,
            client_telegram_id=user.telegram_id,
            status=BookingStatus.ACTIVE,
            created_at=booking.created_at,
        )

    logger.info(
        "Booking %s created: user=%s service=%s start=%s",
        info.id, telegram_id, service_id, start_at.isoformat(),
    )
    return info


async def list_user_bookings(
    session: AsyncSession, telegram_id: int, now: datetime | None = None
) -> list[BookingInfo]:
    """Return a user's upcoming active bookings."""
    bookings = await repo.list_user_upcoming_bookings(session, telegram_id, now or utcnow())
    return [to_booking_info(b) for b in bookings]


async def get_user_booking(
    session: AsyncSession, telegram_id: int, booking_id: int
) -> BookingInfo:
    """Return a user's active booking or raise BookingNotFoundError."""
    booking = await repo.get_booking(session, booking_id)
    if (
        booking is None
        or booking.user.telegram_id != telegram_id
        or booking.status != BookingStatus.ACTIVE
    ):
        raise BookingNotFoundError(f"Booking {booking_id} not found for user {telegram_id}")
    return to_booking_info(booking)


async def list_day_bookings(session: AsyncSession, day: date) -> list[BookingInfo]:
    """Return all active bookings on a local calendar day (for admins)."""
    tz = settings.tz
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    bookings = await repo.get_active_bookings_between(session, start, start + timedelta(days=1))
    return [to_booking_info(b) for b in bookings]


async def get_active_booking(session: AsyncSession, booking_id: int) -> BookingInfo:
    """Return any active booking (admin view) or raise BookingNotFoundError."""
    booking = await repo.get_booking(session, booking_id)
    if booking is None or booking.status != BookingStatus.ACTIVE:
        raise BookingNotFoundError(f"Booking {booking_id} not found")
    return to_booking_info(booking)


async def _cancel(session: AsyncSession, booking: Booking) -> BookingInfo:
    if await repo.set_status_if_active(session, [booking.id], BookingStatus.CANCELLED) != 1:
        await session.rollback()
        raise BookingNotFoundError(f"Booking {booking.id} is no longer active")
    await session.commit()
    return replace(to_booking_info(booking), status=BookingStatus.CANCELLED)


async def cancel_by_user(
    session: AsyncSession, telegram_id: int, booking_id: int, now: datetime | None = None
) -> BookingInfo:
    """Cancel a user's own future booking; the slot becomes free immediately."""
    now = now or utcnow()
    booking = await repo.get_booking(session, booking_id)
    if (
        booking is None
        or booking.user.telegram_id != telegram_id
        or booking.status != BookingStatus.ACTIVE
    ):
        raise BookingNotFoundError(f"Booking {booking_id} not found for user {telegram_id}")
    if booking.start_at <= now:
        raise BookingNotCancellableError(f"Booking {booking_id} has already started")
    info = await _cancel(session, booking)
    logger.info("Booking %s cancelled by user %s", booking_id, telegram_id)
    return info


async def cancel_by_admin(session: AsyncSession, booking_id: int) -> BookingInfo:
    """Cancel any active booking (admin action)."""
    booking = await repo.get_booking(session, booking_id)
    if booking is None or booking.status != BookingStatus.ACTIVE:
        raise BookingNotFoundError(f"Booking {booking_id} not found")
    info = await _cancel(session, booking)
    logger.info("Booking %s cancelled by admin", booking_id)
    return info


async def list_pending_reminders(
    session: AsyncSession, now: datetime | None = None
) -> list[BookingInfo]:
    """Return future active bookings that still need a reminder."""
    bookings = await repo.list_pending_reminders(session, now or utcnow())
    return [to_booking_info(b) for b in bookings]


async def claim_reminder(
    session: AsyncSession, booking_id: int, now: datetime | None = None
) -> BookingInfo | None:
    """Mark the reminder as sent and return the booking, or None if not needed.

    The flag is set before sending (at-most-once): a duplicate reminder is
    worse for the client than a missing one.
    """
    now = now or utcnow()
    booking = await repo.get_booking(session, booking_id)
    if booking is None or booking.start_at <= now:
        return None
    if not await repo.claim_reminder(session, booking_id):
        await session.rollback()
        return None
    await session.commit()
    return to_booking_info(booking)


async def complete_finished_bookings(
    session: AsyncSession, now: datetime | None = None
) -> list[BookingInfo]:
    """Mark active bookings that have ended as DONE; return them."""
    finished = await repo.list_finished_active(session, now or utcnow())
    if not finished:
        return []
    await repo.set_status_if_active(session, [b.id for b in finished], BookingStatus.DONE)
    await session.commit()
    logger.info("Marked %d bookings as done", len(finished))
    return [replace(to_booking_info(b), status=BookingStatus.DONE) for b in finished]
