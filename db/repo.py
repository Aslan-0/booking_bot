"""Data-access functions. The only place where SQL queries live."""

import logging
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import Booking, BookingStatus, Service, User

logger = logging.getLogger(__name__)

DEMO_SERVICES: tuple[tuple[str, int, int], ...] = (
    ("Men's haircut", 60, 1500),
    ("Beard trim", 30, 800),
    ("Haircut + beard", 90, 2000),
)


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    """Return a user by Telegram id or None."""
    return await session.scalar(select(User).where(User.telegram_id == telegram_id))


async def add_user(session: AsyncSession, telegram_id: int, name: str) -> User:
    """Add a new user to the session and flush to get its id (no commit)."""
    user = User(telegram_id=telegram_id, name=name)
    session.add(user)
    await session.flush()
    logger.info("New user registered: telegram_id=%s", telegram_id)
    return user


async def get_or_create_user(session: AsyncSession, telegram_id: int, name: str) -> User:
    """Return a user by telegram_id, creating it (or refreshing the name) if needed."""
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        user = await add_user(session, telegram_id, name)
    elif user.name != name:
        user.name = name
    await session.commit()
    return user


async def list_active_services(session: AsyncSession) -> list[Service]:
    """Return active services ordered by id."""
    result = await session.scalars(
        select(Service).where(Service.is_active.is_(True)).order_by(Service.id)
    )
    return list(result)


async def get_service(session: AsyncSession, service_id: int) -> Service | None:
    """Return a service by id or None."""
    return await session.get(Service, service_id)


async def seed_services(session: AsyncSession) -> None:
    """Insert demo services if the services table is empty."""
    count = await session.scalar(select(func.count()).select_from(Service))
    if count:
        return
    session.add_all(
        Service(title=title, duration_min=duration, price=price)
        for title, duration, price in DEMO_SERVICES
    )
    await session.commit()
    logger.info("Seeded %d demo services", len(DEMO_SERVICES))


def _with_relations() -> Select[tuple[Booking]]:
    """SELECT bookings with user and service eagerly loaded (no lazy IO in async)."""
    return select(Booking).options(selectinload(Booking.user), selectinload(Booking.service))


async def get_booking(session: AsyncSession, booking_id: int) -> Booking | None:
    """Return a booking with user and service loaded, or None."""
    return await session.scalar(
        _with_relations().where(Booking.id == booking_id).execution_options(populate_existing=True)
    )


async def get_active_bookings_between(
    session: AsyncSession, start: datetime, end: datetime
) -> list[Booking]:
    """Return active bookings that intersect the interval [start, end)."""
    result = await session.scalars(
        _with_relations()
        .where(
            Booking.status == BookingStatus.ACTIVE,
            Booking.start_at < end,
            Booking.end_at > start,
        )
        .order_by(Booking.start_at)
    )
    return list(result)


async def list_user_upcoming_bookings(
    session: AsyncSession, telegram_id: int, after: datetime
) -> list[Booking]:
    """Return a user's active bookings that start after `after`."""
    result = await session.scalars(
        _with_relations()
        .join(Booking.user)
        .where(
            User.telegram_id == telegram_id,
            Booking.status == BookingStatus.ACTIVE,
            Booking.start_at > after,
        )
        .order_by(Booking.start_at)
    )
    return list(result)


async def list_pending_reminders(session: AsyncSession, after: datetime) -> list[Booking]:
    """Return active future bookings whose reminder has not been sent yet."""
    result = await session.scalars(
        _with_relations().where(
            Booking.status == BookingStatus.ACTIVE,
            Booking.reminder_sent.is_(False),
            Booking.start_at > after,
        )
    )
    return list(result)


async def list_finished_active(session: AsyncSession, before: datetime) -> list[Booking]:
    """Return active bookings that ended at or before `before`."""
    result = await session.scalars(
        _with_relations().where(
            Booking.status == BookingStatus.ACTIVE,
            Booking.end_at <= before,
        )
    )
    return list(result)


async def add_booking(
    session: AsyncSession,
    user_id: int,
    service_id: int,
    start_at: datetime,
    end_at: datetime,
    reminder_sent: bool = False,
) -> Booking:
    """Add an active booking and flush to get its id (no commit)."""
    booking = Booking(
        user_id=user_id,
        service_id=service_id,
        start_at=start_at,
        end_at=end_at,
        status=BookingStatus.ACTIVE,
        reminder_sent=reminder_sent,
    )
    session.add(booking)
    await session.flush()
    return booking


async def set_status_if_active(
    session: AsyncSession, booking_ids: Sequence[int], status: BookingStatus
) -> int:
    """Atomically change status of still-active bookings; return affected row count."""
    if not booking_ids:
        return 0
    result = await session.execute(
        update(Booking)
        .where(Booking.id.in_(booking_ids), Booking.status == BookingStatus.ACTIVE)
        .values(status=status)
    )
    return result.rowcount


async def claim_reminder(session: AsyncSession, booking_id: int) -> bool:
    """Atomically mark a reminder as sent; False if already sent or not active."""
    result = await session.execute(
        update(Booking)
        .where(
            Booking.id == booking_id,
            Booking.status == BookingStatus.ACTIVE,
            Booking.reminder_sent.is_(False),
        )
        .values(reminder_sent=True)
    )
    return result.rowcount == 1
