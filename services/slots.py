"""Pure slot calculation: no DB, no aiogram, fully deterministic.

All public functions take aware datetimes. Returned slot starts are in UTC;
convert them to the business time zone only for display.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def _ensure_aware(value: datetime, name: str) -> None:
    """Raise ValueError if a datetime has no tzinfo."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got naive {value!r}")


@dataclass(frozen=True, slots=True)
class WorkHours:
    """Daily working window in local business time and slot step."""

    start: time
    end: time
    step_min: int

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(f"Work start {self.start} must be before end {self.end}")
        if self.step_min <= 0:
            raise ValueError(f"Slot step must be positive, got {self.step_min}")


@dataclass(frozen=True, slots=True)
class Interval:
    """Half-open time interval [start, end) with aware datetimes."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        _ensure_aware(self.start, "start")
        _ensure_aware(self.end, "end")
        if self.start >= self.end:
            raise ValueError(f"Interval start {self.start} must be before end {self.end}")


def overlaps(a: Interval, b: Interval) -> bool:
    """Return True if two half-open intervals intersect (touching is not overlap)."""
    return a.start < b.end and b.start < a.end


def booking_dates(now: datetime, tz: ZoneInfo, days_ahead: int) -> list[date]:
    """Return `days_ahead` consecutive local dates starting from today in `tz`."""
    _ensure_aware(now, "now")
    today = now.astimezone(tz).date()
    return [today + timedelta(days=offset) for offset in range(days_ahead)]


def working_window(day: date, hours: WorkHours, tz: ZoneInfo) -> Interval:
    """Return the working interval of a local `day` as aware datetimes in `tz`."""
    return Interval(
        start=datetime.combine(day, hours.start, tzinfo=tz),
        end=datetime.combine(day, hours.end, tzinfo=tz),
    )


def free_slots(
    day: date,
    duration_min: int,
    busy: Iterable[Interval],
    hours: WorkHours,
    tz: ZoneInfo,
    now: datetime,
) -> list[datetime]:
    """Return UTC start times on local `day` where a service fits.

    A slot is free when it:
    * starts on the step grid from the start of the working day (local time),
    * ends no later than the end of the working day,
    * starts strictly after `now`,
    * does not overlap any busy interval.
    """
    if duration_min <= 0:
        raise ValueError(f"Duration must be positive, got {duration_min}")
    _ensure_aware(now, "now")

    window = working_window(day, hours, tz)
    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=hours.step_min)
    day_busy = [interval for interval in busy if overlaps(interval, window)]

    result: list[datetime] = []
    local_start = window.start
    while True:
        start_utc = local_start.astimezone(timezone.utc)
        candidate = Interval(start_utc, start_utc + duration)
        if candidate.end > window.end:
            break
        if candidate.start > now and not any(overlaps(candidate, b) for b in day_busy):
            result.append(start_utc)
        local_start += step
    return result
