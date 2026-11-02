"""Human-readable formatting of dates, times and prices (English UI)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

WEEKDAYS_SHORT: tuple[str, ...] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def format_day(day: date) -> str:
    """'Mon 02.11'."""
    return f"{WEEKDAYS_SHORT[day.weekday()]} {day:%d.%m}"


def format_date(dt: datetime, tz: ZoneInfo) -> str:
    """'Mon, 02.11.2026' in the given time zone."""
    local = dt.astimezone(tz)
    return f"{WEEKDAYS_SHORT[local.weekday()]}, {local:%d.%m.%Y}"


def format_time(dt: datetime, tz: ZoneInfo) -> str:
    """'14:30' in the given time zone."""
    return dt.astimezone(tz).strftime("%H:%M")


def format_price(price: int) -> str:
    """'1 500 ₽'."""
    return f"{price:,}".replace(",", " ") + " ₽"
