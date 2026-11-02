"""Integration tests for booking_service on a real (temporary) SQLite file."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from config import settings
from db.models import BookingStatus
from db.repo import seed_services
from db.session import create_engine, create_sessionmaker, init_db
from services import booking_service as bs
from services.booking_service import (
    BookingInfo,
    BookingNotCancellableError,
    BookingNotFoundError,
    SlotUnavailableError,
)
from services.slots import booking_dates

Sessionmaker = async_sessionmaker[AsyncSession]


def run_with_db(tmp_path: Path, scenario: Callable[[Sessionmaker], Awaitable[None]]) -> None:
    """Run an async scenario against a fresh seeded database (one event loop per test)."""

    async def runner() -> None:
        engine = create_engine(f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
        sessionmaker = create_sessionmaker(engine)
        await init_db(engine)
        async with sessionmaker() as session:
            await seed_services(session)
        try:
            await scenario(sessionmaker)
        finally:
            await engine.dispose()

    asyncio.run(runner())


async def first_slot(sm: Sessionmaker, days_ahead: int = 2) -> tuple[int, datetime]:
    """Return (service_id of a 60-min service, its first free slot N days ahead)."""
    async with sm() as session:
        service = (await bs.list_services(session))[0]
        day = booking_dates(datetime.now(timezone.utc), settings.tz, days_ahead + 1)[days_ahead]
        return service.id, (await bs.get_free_slots(session, service.id, day))[0]


async def book(sm: Sessionmaker, telegram_id: int, service_id: int, slot: datetime) -> BookingInfo:
    async with sm() as session:
        return await bs.create_booking(session, telegram_id, f"U{telegram_id}", service_id, slot)


def test_concurrent_bookings_same_slot(tmp_path: Path) -> None:
    async def scenario(sm: Sessionmaker) -> None:
        service_id, slot = await first_slot(sm)
        results = await asyncio.gather(
            *(book(sm, i, service_id, slot) for i in range(1, 6)), return_exceptions=True
        )
        ok = [r for r in results if isinstance(r, BookingInfo)]
        taken = [r for r in results if isinstance(r, SlotUnavailableError)]
        assert len(ok) == 1, results
        assert len(taken) == 4, results

        async with sm() as session:
            day = slot.astimezone(settings.tz).date()
            free = await bs.get_free_slots(session, service_id, day)
        assert slot not in free
        assert slot + timedelta(minutes=30) not in free
        assert slot + timedelta(minutes=60) in free

    run_with_db(tmp_path, scenario)


def test_cancel_frees_slot_and_checks_owner(tmp_path: Path) -> None:
    async def scenario(sm: Sessionmaker) -> None:
        service_id, slot = await first_slot(sm)
        info = await book(sm, 1, service_id, slot)

        async with sm() as session:
            with pytest.raises(BookingNotFoundError):
                await bs.cancel_by_user(session, 2, info.id)
            cancelled = await bs.cancel_by_user(session, 1, info.id)
            assert cancelled.status is BookingStatus.CANCELLED
            with pytest.raises(BookingNotFoundError):
                await bs.cancel_by_user(session, 1, info.id)
            assert await bs.list_user_bookings(session, 1) == []

        again = await book(sm, 2, service_id, slot)
        assert again.start_at == slot

    run_with_db(tmp_path, scenario)


def test_started_booking_cannot_be_cancelled_by_client(tmp_path: Path) -> None:
    async def scenario(sm: Sessionmaker) -> None:
        service_id, slot = await first_slot(sm)
        info = await book(sm, 1, service_id, slot)
        async with sm() as session:
            with pytest.raises(BookingNotCancellableError):
                await bs.cancel_by_user(session, 1, info.id, now=slot + timedelta(minutes=5))
            admin_cancelled = await bs.cancel_by_admin(session, info.id)
            assert admin_cancelled.status is BookingStatus.CANCELLED

    run_with_db(tmp_path, scenario)


def test_reminder_is_claimed_only_once(tmp_path: Path) -> None:
    async def scenario(sm: Sessionmaker) -> None:
        service_id, slot = await first_slot(sm)
        info = await book(sm, 1, service_id, slot)
        async with sm() as session:
            assert [b.id for b in await bs.list_pending_reminders(session)] == [info.id]
            assert await bs.claim_reminder(session, info.id) is not None
            assert await bs.claim_reminder(session, info.id) is None
            assert await bs.list_pending_reminders(session) == []

    run_with_db(tmp_path, scenario)


def test_cancelled_booking_gets_no_reminder(tmp_path: Path) -> None:
    async def scenario(sm: Sessionmaker) -> None:
        service_id, slot = await first_slot(sm)
        info = await book(sm, 1, service_id, slot)
        async with sm() as session:
            await bs.cancel_by_user(session, 1, info.id)
            assert await bs.claim_reminder(session, info.id) is None

    run_with_db(tmp_path, scenario)


def test_last_minute_booking_skips_reminder(tmp_path: Path) -> None:
    async def scenario(sm: Sessionmaker) -> None:
        service_id, slot = await first_slot(sm)
        now = slot - timedelta(minutes=settings.reminder_before_min - 10)
        async with sm() as session:
            await bs.create_booking(session, 1, "U1", service_id, slot, now=now)
            assert await bs.list_pending_reminders(session, now=now) == []

    run_with_db(tmp_path, scenario)


def test_finished_bookings_are_marked_done(tmp_path: Path) -> None:
    async def scenario(sm: Sessionmaker) -> None:
        service_id, slot = await first_slot(sm)
        info = await book(sm, 1, service_id, slot)
        async with sm() as session:
            assert await bs.complete_finished_bookings(session) == []
            done = await bs.complete_finished_bookings(session, now=info.end_at)
            assert [(b.id, b.status) for b in done] == [(info.id, BookingStatus.DONE)]
            with pytest.raises(BookingNotFoundError):
                await bs.get_active_booking(session, info.id)

    run_with_db(tmp_path, scenario)


def test_admin_day_view(tmp_path: Path) -> None:
    async def scenario(sm: Sessionmaker) -> None:
        service_id, slot = await first_slot(sm)
        info = await book(sm, 1, service_id, slot)
        day = slot.astimezone(settings.tz).date()
        async with sm() as session:
            assert [b.id for b in await bs.list_day_bookings(session, day)] == [info.id]
            assert await bs.list_day_bookings(session, day + timedelta(days=1)) == []

    run_with_db(tmp_path, scenario)
