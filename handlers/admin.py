"""Admin panel: bookings for today / tomorrow and cancellation.

AdminFilter is attached to the whole router, so every handler here
is reachable only by users from ADMIN_IDS.
"""

import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from filters.admin import AdminFilter
from keyboards.callbacks import AdminAction, AdminCallback
from keyboards.inline import admin_cancel_confirm_kb, admin_day_kb, admin_menu_kb
from services import booking_service
from services.booking_events import BookingEvents
from services.booking_service import BookingInfo, BookingNotFoundError
from utils.formatting import format_date, format_day, format_price, format_time

logger = logging.getLogger(__name__)
router = Router(name="admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

ADMIN_MENU_TEXT = "<b>Admin panel</b>\n\nChoose a day:"
DAY_NAMES: tuple[str, ...] = ("today", "tomorrow")


def _day_text(bookings: list[BookingInfo], day_offset: int) -> str:
    tz = settings.tz
    day = booking_service.local_day(day_offset)
    header = f"<b>Bookings for {DAY_NAMES[day_offset]}, {format_day(day)}</b>"
    if not bookings:
        return f"{header}\n\nNo bookings."
    lines = [header, ""]
    for b in bookings:
        name = html.escape(b.client_name)
        lines.append(
            f"<b>{format_time(b.start_at, tz)}–{format_time(b.end_at, tz)}</b> · "
            f"{b.service_title} · {format_price(b.price)}\n"
            f'    <a href="tg://user?id={b.client_telegram_id}">{name}</a>'
        )
    total = sum(b.price for b in bookings)
    lines.append(f"\nTotal: {len(bookings)} · {format_price(total)}")
    return "\n".join(lines)


async def _edit(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=markup)


async def _show_day(callback: CallbackQuery, session: AsyncSession, day_offset: int) -> None:
    day = booking_service.local_day(day_offset)
    bookings = await booking_service.list_day_bookings(session, day)
    await _edit(
        callback,
        _day_text(bookings, day_offset),
        admin_day_kb(bookings, day_offset, settings.tz, booking_service.utcnow()),
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    """Open the admin panel."""
    await state.clear()
    await message.answer(ADMIN_MENU_TEXT, reply_markup=admin_menu_kb())


@router.callback_query(AdminCallback.filter(F.action == AdminAction.MENU))
async def admin_menu(callback: CallbackQuery) -> None:
    """Back to the admin panel."""
    await _edit(callback, ADMIN_MENU_TEXT, admin_menu_kb())
    await callback.answer()


@router.callback_query(AdminCallback.filter(F.action == AdminAction.DAY))
async def admin_day(
    callback: CallbackQuery, callback_data: AdminCallback, session: AsyncSession
) -> None:
    """Bookings for today or tomorrow."""
    await _show_day(callback, session, callback_data.day_offset)
    await callback.answer()


@router.callback_query(AdminCallback.filter(F.action == AdminAction.ASK_CANCEL))
async def admin_ask_cancel(
    callback: CallbackQuery, callback_data: AdminCallback, session: AsyncSession
) -> None:
    """Ask the admin to confirm cancellation."""
    try:
        b = await booking_service.get_active_booking(session, callback_data.booking_id)
    except BookingNotFoundError:
        await callback.answer("This booking is no longer active", show_alert=True)
        await _show_day(callback, session, callback_data.day_offset)
        return
    tz = settings.tz
    await _edit(
        callback,
        f"Cancel booking #{b.id}?\n\n"
        f"{html.escape(b.client_name)}\n{b.service_title}\n"
        f"{format_date(b.start_at, tz)}, {format_time(b.start_at, tz)}\n\n"
        "The client will be notified.",
        admin_cancel_confirm_kb(b.id, callback_data.day_offset),
    )
    await callback.answer()


@router.callback_query(AdminCallback.filter(F.action == AdminAction.CANCEL))
async def admin_cancel(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    session: AsyncSession,
    events: BookingEvents,
) -> None:
    """Cancel a booking and notify the client."""
    try:
        info = await booking_service.cancel_by_admin(session, callback_data.booking_id)
    except BookingNotFoundError:
        await callback.answer("This booking is no longer active", show_alert=True)
    else:
        await events.on_cancelled(info, by_admin=True)
        logger.info("Admin %s cancelled booking %s", callback.from_user.id, info.id)
        await callback.answer("Booking cancelled")
    await _show_day(callback, session, callback_data.day_offset)
