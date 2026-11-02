"""Inline keyboard builders."""

from collections.abc import Sequence
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.callbacks import (
    AdminAction,
    AdminCallback,
    BackCallback,
    BackTarget,
    ConfirmAction,
    ConfirmCallback,
    DateCallback,
    MenuAction,
    MenuCallback,
    MyBookingAction,
    MyBookingCallback,
    ServiceCallback,
    TimeCallback,
)
from services.booking_service import BookingInfo, ServiceInfo
from utils.formatting import format_day, format_price, format_time

DATES_PER_ROW = 3
TIMES_PER_ROW = 4
ADMIN_DAY_LABELS: tuple[str, ...] = ("Today", "Tomorrow")


def _back_button(target: BackTarget) -> InlineKeyboardButton:
    """'Back' button for the booking flow."""
    return InlineKeyboardButton(text="Back", callback_data=BackCallback(to=target).pack())


def _home_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="Main menu", callback_data=MenuCallback(action=MenuAction.HOME).pack()
    )


def main_menu_kb() -> InlineKeyboardMarkup:
    """Main menu: book a service / my bookings."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Book", callback_data=MenuCallback(action=MenuAction.BOOK))
    builder.button(text="My bookings", callback_data=MenuCallback(action=MenuAction.MY_BOOKINGS))
    builder.adjust(1)
    return builder.as_markup()


def services_kb(services: Sequence[ServiceInfo]) -> InlineKeyboardMarkup:
    """One button per service plus 'to menu'."""
    builder = InlineKeyboardBuilder()
    for service in services:
        builder.button(
            text=f"{service.title} · {service.duration_min} min · {format_price(service.price)}",
            callback_data=ServiceCallback(service_id=service.id),
        )
    builder.adjust(1)
    builder.row(_home_button())
    return builder.as_markup()


def dates_kb(dates: Sequence[date]) -> InlineKeyboardMarkup:
    """Grid of available dates plus 'back'."""
    builder = InlineKeyboardBuilder()
    for day in dates:
        builder.button(text=format_day(day), callback_data=DateCallback(day=day.isoformat()))
    builder.adjust(DATES_PER_ROW)
    builder.row(_back_button(BackTarget.SERVICES))
    return builder.as_markup()


def times_kb(slots: Sequence[datetime], tz: ZoneInfo) -> InlineKeyboardMarkup:
    """Grid of free time slots (shown in local time) plus 'back'."""
    builder = InlineKeyboardBuilder()
    for slot in slots:
        builder.button(
            text=format_time(slot, tz),
            callback_data=TimeCallback(ts=int(slot.timestamp())),
        )
    builder.adjust(TIMES_PER_ROW)
    builder.row(_back_button(BackTarget.DATES))
    return builder.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    """Confirm / cancel / back."""
    builder = InlineKeyboardBuilder()
    builder.button(text="Confirm", callback_data=ConfirmCallback(action=ConfirmAction.YES))
    builder.button(text="Cancel", callback_data=ConfirmCallback(action=ConfirmAction.NO))
    builder.adjust(2)
    builder.row(_back_button(BackTarget.TIMES))
    return builder.as_markup()


def my_bookings_kb(bookings: Sequence[BookingInfo], tz: ZoneInfo) -> InlineKeyboardMarkup:
    """One 'cancel' button per booking plus navigation."""
    builder = InlineKeyboardBuilder()
    for booking in bookings:
        builder.button(
            text=f"Cancel {booking.start_at.astimezone(tz):%d.%m} "
                 f"{format_time(booking.start_at, tz)}"
                 f" · {booking.service_title}",
            callback_data=MyBookingCallback(
                action=MyBookingAction.ASK_CANCEL, booking_id=booking.id
            ),
        )
    if not bookings:
        builder.button(text="Book", callback_data=MenuCallback(action=MenuAction.BOOK))
    builder.adjust(1)
    builder.row(_home_button())
    return builder.as_markup()


def my_cancel_confirm_kb(booking_id: int) -> InlineKeyboardMarkup:
    """Confirm cancellation of the client's booking."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Yes, cancel",
        callback_data=MyBookingCallback(action=MyBookingAction.CANCEL, booking_id=booking_id),
    )
    builder.button(
        text="No, go back",
        callback_data=MyBookingCallback(action=MyBookingAction.LIST),
    )
    builder.adjust(1)
    return builder.as_markup()


def admin_menu_kb() -> InlineKeyboardMarkup:
    """Admin panel: today / tomorrow."""
    builder = InlineKeyboardBuilder()
    for offset, label in enumerate(ADMIN_DAY_LABELS):
        builder.button(
            text=label, callback_data=AdminCallback(action=AdminAction.DAY, day_offset=offset)
        )
    builder.adjust(2)
    return builder.as_markup()


def admin_day_kb(
    bookings: Sequence[BookingInfo], day_offset: int, tz: ZoneInfo, now: datetime
) -> InlineKeyboardMarkup:
    """Cancel buttons for not-yet-started bookings of the day plus navigation."""
    builder = InlineKeyboardBuilder()
    for booking in bookings:
        if booking.start_at <= now:
            continue
        builder.button(
            text=f"Cancel {format_time(booking.start_at, tz)} · {booking.client_name}",
            callback_data=AdminCallback(
                action=AdminAction.ASK_CANCEL, day_offset=day_offset, booking_id=booking.id
            ),
        )
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="Refresh",
            callback_data=AdminCallback(action=AdminAction.DAY, day_offset=day_offset).pack(),
        ),
        InlineKeyboardButton(
            text="Back", callback_data=AdminCallback(action=AdminAction.MENU).pack()
        ),
    )
    return builder.as_markup()


def admin_cancel_confirm_kb(booking_id: int, day_offset: int) -> InlineKeyboardMarkup:
    """Confirm cancellation of any booking by an admin."""
    builder = InlineKeyboardBuilder()
    builder.button(
        text="Yes, cancel",
        callback_data=AdminCallback(
            action=AdminAction.CANCEL, day_offset=day_offset, booking_id=booking_id
        ),
    )
    builder.button(
        text="No, go back",
        callback_data=AdminCallback(action=AdminAction.DAY, day_offset=day_offset),
    )
    builder.adjust(1)
    return builder.as_markup()
