"""'My bookings': list of the client's upcoming bookings and cancellation."""

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from keyboards.callbacks import MenuAction, MenuCallback, MyBookingAction, MyBookingCallback
from keyboards.inline import my_bookings_kb, my_cancel_confirm_kb
from services import booking_service
from services.booking_events import BookingEvents
from services.booking_service import (
    BookingInfo,
    BookingNotCancellableError,
    BookingNotFoundError,
)
from utils.formatting import format_date, format_price, format_time

logger = logging.getLogger(__name__)
router = Router(name="my_bookings")


def _bookings_text(bookings: list[BookingInfo]) -> str:
    if not bookings:
        return "You have no active bookings."
    tz = settings.tz
    lines = ["<b>Your bookings:</b>\n"]
    for number, b in enumerate(bookings, start=1):
        lines.append(
            f"{number}. <b>{b.service_title}</b>\n"
            f"    {format_date(b.start_at, tz)}, "
            f"{format_time(b.start_at, tz)}–{format_time(b.end_at, tz)}\n"
            f"    {format_price(b.price)}"
        )
    lines.append("\nTo cancel a booking, tap it below.")
    return "\n".join(lines)


async def _show_list(callback: CallbackQuery, session: AsyncSession) -> None:
    bookings = await booking_service.list_user_bookings(session, callback.from_user.id)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _bookings_text(bookings), reply_markup=my_bookings_kb(bookings, settings.tz)
        )


@router.callback_query(MenuCallback.filter(F.action == MenuAction.MY_BOOKINGS))
@router.callback_query(MyBookingCallback.filter(F.action == MyBookingAction.LIST))
async def show_my_bookings(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    """Show the client's upcoming bookings."""
    await state.clear()
    await _show_list(callback, session)
    await callback.answer()


@router.callback_query(MyBookingCallback.filter(F.action == MyBookingAction.ASK_CANCEL))
async def ask_cancel(
    callback: CallbackQuery, callback_data: MyBookingCallback, session: AsyncSession
) -> None:
    """Ask the client to confirm cancellation."""
    try:
        booking = await booking_service.get_user_booking(
            session, callback.from_user.id, callback_data.booking_id
        )
    except BookingNotFoundError:
        await callback.answer("This booking is no longer active", show_alert=True)
        await _show_list(callback, session)
        return
    tz = settings.tz
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Cancel this booking?\n\n"
            f"<b>{booking.service_title}</b>\n"
            f"{format_date(booking.start_at, tz)}, {format_time(booking.start_at, tz)}",
            reply_markup=my_cancel_confirm_kb(booking.id),
        )
    await callback.answer()


@router.callback_query(MyBookingCallback.filter(F.action == MyBookingAction.CANCEL))
async def cancel(
    callback: CallbackQuery,
    callback_data: MyBookingCallback,
    session: AsyncSession,
    events: BookingEvents,
) -> None:
    """Cancel the booking, free the slot and refresh the list."""
    try:
        info = await booking_service.cancel_by_user(
            session, callback.from_user.id, callback_data.booking_id
        )
    except BookingNotCancellableError:
        await callback.answer(
            "This booking has already started and cannot be cancelled", show_alert=True
        )
    except BookingNotFoundError:
        await callback.answer("This booking is no longer active", show_alert=True)
    else:
        await events.on_cancelled(info, by_admin=False)
        await callback.answer("Booking cancelled")
    await _show_list(callback, session)
