"""Booking flow (FSM): service -> date -> time -> confirmation."""

import logging
from datetime import date, datetime, timezone

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from keyboards.callbacks import (
    BackCallback,
    BackTarget,
    ConfirmAction,
    ConfirmCallback,
    DateCallback,
    MenuAction,
    MenuCallback,
    ServiceCallback,
    TimeCallback,
)
from keyboards.inline import confirm_kb, dates_kb, main_menu_kb, services_kb, times_kb
from services import booking_service
from services.booking_events import BookingEvents
from services.booking_service import ServiceNotFoundError, SlotUnavailableError
from states.booking import BookingStates
from utils.formatting import format_date, format_day, format_price, format_time

logger = logging.getLogger(__name__)
router = Router(name="booking")


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Replace the message with the current step's screen."""
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=markup)


async def _show_services(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    services = await booking_service.list_services(session)
    if not services:
        await callback.answer("No services are available right now", show_alert=True)
        return
    await state.set_state(BookingStates.choosing_service)
    await _edit(callback, "Choose a service:", services_kb(services))
    await callback.answer()


async def _show_dates(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, service_id: int
) -> None:
    try:
        service = await booking_service.get_service_info(session, service_id)
        dates = await booking_service.get_dates_with_free_slots(session, service_id)
    except ServiceNotFoundError:
        await callback.answer("This service is no longer available", show_alert=True)
        await _show_services(callback, state, session)
        return
    if not dates:
        await callback.answer(
            f"No free time in the next {settings.booking_days_ahead} days",
            show_alert=True,
        )
        return
    await state.set_state(BookingStates.choosing_date)
    await _edit(callback, f"<b>{service.title}</b>\n\nChoose a date:", dates_kb(dates))
    await callback.answer()


async def _show_times(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, service_id: int, day: date
) -> None:
    slots = await booking_service.get_free_slots(session, service_id, day)
    if not slots:
        await callback.answer("No free time left on this date", show_alert=True)
        await _show_dates(callback, state, session, service_id)
        return
    await state.set_state(BookingStates.choosing_time)
    await _edit(
        callback,
        f"{format_day(day)}\n\nChoose a time:",
        times_kb(slots, settings.tz),
    )
    await callback.answer()


@router.callback_query(MenuCallback.filter(F.action == MenuAction.BOOK))
async def start_booking(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Entry point of the booking flow."""
    await state.clear()
    await _show_services(callback, state, session)


@router.callback_query(BookingStates.choosing_service, ServiceCallback.filter())
async def choose_service(
    callback: CallbackQuery,
    callback_data: ServiceCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Remember the service and show dates."""
    await state.update_data(service_id=callback_data.service_id)
    await _show_dates(callback, state, session, callback_data.service_id)


@router.callback_query(BookingStates.choosing_date, DateCallback.filter())
async def choose_date(
    callback: CallbackQuery,
    callback_data: DateCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Remember the date and show free time slots."""
    data = await state.get_data()
    await state.update_data(day=callback_data.day)
    await _show_times(
        callback, state, session, data["service_id"], date.fromisoformat(callback_data.day)
    )


@router.callback_query(BookingStates.choosing_time, TimeCallback.filter())
async def choose_time(
    callback: CallbackQuery,
    callback_data: TimeCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Remember the slot and ask for confirmation."""
    data = await state.get_data()
    service = await booking_service.get_service_info(session, data["service_id"])
    start = datetime.fromtimestamp(callback_data.ts, tz=timezone.utc)
    await state.update_data(start_ts=callback_data.ts)
    await state.set_state(BookingStates.confirming)
    tz = settings.tz
    text = (
        "Please review your booking:\n\n"
        f"<b>{service.title}</b> ({service.duration_min} min)\n"
        f"{format_date(start, tz)}\n"
        f"{format_time(start, tz)}\n"
        f"{format_price(service.price)}"
    )
    await _edit(callback, text, confirm_kb())
    await callback.answer()


@router.callback_query(BookingStates.confirming, ConfirmCallback.filter(F.action == ConfirmAction.YES))
async def confirm_booking(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, events: BookingEvents
) -> None:
    """Create the booking; if the slot was taken meanwhile, offer other times."""
    data = await state.get_data()
    start = datetime.fromtimestamp(data["start_ts"], tz=timezone.utc)
    try:
        booking = await booking_service.create_booking(
            session,
            telegram_id=callback.from_user.id,
            user_name=callback.from_user.full_name,
            service_id=data["service_id"],
            start_at=start,
        )
    except SlotUnavailableError:
        await callback.answer(
            "This time was just taken. Please choose another one", show_alert=True
        )
        await _show_times(callback, state, session, data["service_id"], date.fromisoformat(data["day"]))
        return
    except ServiceNotFoundError:
        await callback.answer("This service is no longer available", show_alert=True)
        await _show_services(callback, state, session)
        return

    await state.clear()
    await events.on_created(booking)
    tz = settings.tz
    text = (
        "<b>You are booked!</b>\n\n"
        f"{booking.service_title}\n"
        f"{format_date(booking.start_at, tz)}\n"
        f"{format_time(booking.start_at, tz)}–{format_time(booking.end_at, tz)}\n"
        f"{format_price(booking.price)}\n\n"
        f"I will remind you {settings.reminder_before_min} min before your visit.\n"
        'You can cancel your booking in the "My bookings" section.'
    )
    await _edit(callback, text, main_menu_kb())
    await callback.answer("Done!")


@router.callback_query(BookingStates.confirming, ConfirmCallback.filter(F.action == ConfirmAction.NO))
async def cancel_booking_flow(callback: CallbackQuery, state: FSMContext) -> None:
    """Abort the booking flow and return to the menu."""
    await state.clear()
    await _edit(callback, "Booking was not completed. Choose an action:", main_menu_kb())
    await callback.answer()


@router.callback_query(StateFilter(BookingStates), BackCallback.filter())
async def go_back(
    callback: CallbackQuery,
    callback_data: BackCallback,
    state: FSMContext,
    session: AsyncSession,
) -> None:
    """Navigate one step back inside the booking flow."""
    data = await state.get_data()
    match callback_data.to:
        case BackTarget.SERVICES:
            await _show_services(callback, state, session)
        case BackTarget.DATES:
            await _show_dates(callback, state, session, data["service_id"])
        case BackTarget.TIMES:
            await _show_times(
                callback, state, session, data["service_id"], date.fromisoformat(data["day"])
            )
