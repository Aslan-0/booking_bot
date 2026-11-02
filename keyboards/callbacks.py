"""CallbackData factories. Raw callback strings are not used anywhere else."""

from enum import Enum

from aiogram.filters.callback_data import CallbackData


class MenuAction(str, Enum):
    """Main menu actions."""

    BOOK = "book"
    MY_BOOKINGS = "my"
    HOME = "home"


class MenuCallback(CallbackData, prefix="menu"):
    """Main menu button."""

    action: MenuAction


class ServiceCallback(CallbackData, prefix="svc"):
    """Service chosen."""

    service_id: int


class DateCallback(CallbackData, prefix="day"):
    """Date chosen; `day` is an ISO date (YYYY-MM-DD) in business time zone."""

    day: str


class TimeCallback(CallbackData, prefix="slot"):
    """Time slot chosen; `ts` is the slot start as a UTC unix timestamp."""

    ts: int


class ConfirmAction(str, Enum):
    """Confirmation answers."""

    YES = "yes"
    NO = "no"


class ConfirmCallback(CallbackData, prefix="cfm"):
    """Booking confirmation."""

    action: ConfirmAction


class BackTarget(str, Enum):
    """Steps of the booking flow the user can go back to."""

    SERVICES = "svc"
    DATES = "day"
    TIMES = "slot"


class BackCallback(CallbackData, prefix="back"):
    """'Back' button inside the booking flow."""

    to: BackTarget


class MyBookingAction(str, Enum):
    """Actions in 'My bookings'."""

    LIST = "list"
    ASK_CANCEL = "ask"
    CANCEL = "cancel"


class MyBookingCallback(CallbackData, prefix="my"):
    """Client's own booking action."""

    action: MyBookingAction
    booking_id: int = 0


class AdminAction(str, Enum):
    """Admin panel actions."""

    MENU = "menu"
    DAY = "day"
    ASK_CANCEL = "ask"
    CANCEL = "cancel"


class AdminCallback(CallbackData, prefix="adm"):
    """Admin panel button; `day_offset` 0 = today, 1 = tomorrow."""

    action: AdminAction
    day_offset: int = 0
    booking_id: int = 0
