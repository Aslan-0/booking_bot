"""Tests for services/slots.py: overlaps, working-day bounds, time zones."""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services.slots import (
    Interval,
    WorkHours,
    booking_dates,
    free_slots,
    overlaps,
    working_window,
)

MSK = ZoneInfo("Europe/Moscow")
BERLIN = ZoneInfo("Europe/Berlin")
HOURS = WorkHours(start=time(10, 0), end=time(20, 0), step_min=30)
DAY = date(2026, 11, 10)
LONG_AGO = datetime(2000, 1, 1, tzinfo=timezone.utc)


def msk(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    """Aware Moscow datetime on the test day."""
    return datetime.combine(day, time(hour, minute), tzinfo=MSK)


def local_times(slots: list[datetime], tz: ZoneInfo = MSK) -> list[str]:
    """Render UTC slots as local 'HH:MM' strings for readable asserts."""
    return [s.astimezone(tz).strftime("%H:%M") for s in slots]


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((10, 11), (11, 12), False),
        ((11, 12), (10, 11), False),
        ((10, 12), (11, 13), True),
        ((10, 13), (11, 12), True),
        ((11, 12), (10, 13), True),
        ((10, 11), (10, 11), True),
        ((10, 11), (12, 13), False),
    ],
)
def test_overlaps(a: tuple[int, int], b: tuple[int, int], expected: bool) -> None:
    ia = Interval(msk(a[0]), msk(a[1]))
    ib = Interval(msk(b[0]), msk(b[1]))
    assert overlaps(ia, ib) is expected


def test_overlaps_across_time_zones() -> None:
    """Same instant expressed in different zones must be compared correctly."""
    moscow = Interval(msk(10), msk(11))
    utc = Interval(
        datetime(2026, 11, 10, 7, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 10, 8, 30, tzinfo=timezone.utc),
    )
    assert overlaps(moscow, utc)


def test_interval_rejects_naive_and_empty() -> None:
    with pytest.raises(ValueError):
        Interval(datetime(2026, 1, 1, 10), datetime(2026, 1, 1, 11))
    with pytest.raises(ValueError):
        Interval(msk(10), msk(10))


def test_empty_day_first_and_last_slot() -> None:
    slots = free_slots(DAY, 60, [], HOURS, MSK, LONG_AGO)
    times = local_times(slots)
    assert times[0] == "10:00"
    assert times[-1] == "19:00"
    assert len(times) == 19


def test_duration_not_multiple_of_step() -> None:
    times = local_times(free_slots(DAY, 90, [], HOURS, MSK, LONG_AGO))
    assert times[-1] == "18:30"
    assert "19:00" not in times


def test_service_longer_than_working_day() -> None:
    assert free_slots(DAY, 11 * 60, [], HOURS, MSK, LONG_AGO) == []


def test_service_exactly_whole_day() -> None:
    assert local_times(free_slots(DAY, 10 * 60, [], HOURS, MSK, LONG_AGO)) == ["10:00"]


def test_invalid_work_hours_and_duration() -> None:
    with pytest.raises(ValueError):
        WorkHours(time(20, 0), time(10, 0), 30)
    with pytest.raises(ValueError):
        WorkHours(time(10, 0), time(20, 0), 0)
    with pytest.raises(ValueError):
        free_slots(DAY, 0, [], HOURS, MSK, LONG_AGO)


def test_busy_interval_excludes_overlapping_slots_only() -> None:
    busy = [Interval(msk(12), msk(13))]
    times = local_times(free_slots(DAY, 60, busy, HOURS, MSK, LONG_AGO))
    assert "11:00" in times
    assert "11:30" not in times
    assert "12:00" not in times
    assert "12:30" not in times
    assert "13:00" in times


def test_short_gap_does_not_fit_long_service() -> None:
    busy = [Interval(msk(10), msk(12)), Interval(msk(12, 30), msk(20))]
    assert free_slots(DAY, 60, busy, HOURS, MSK, LONG_AGO) == []
    assert local_times(free_slots(DAY, 30, busy, HOURS, MSK, LONG_AGO)) == ["12:00"]


def test_busy_on_other_day_is_ignored() -> None:
    other_day = DAY + timedelta(days=1)
    busy = [Interval(msk(10, day=other_day), msk(20, day=other_day))]
    assert len(free_slots(DAY, 60, busy, HOURS, MSK, LONG_AGO)) == 19


def test_busy_stored_in_utc_blocks_local_slot() -> None:
    """A booking stored as 07:00-08:00 UTC is 10:00-11:00 in Moscow."""
    busy = [
        Interval(
            datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc),
        )
    ]
    times = local_times(free_slots(DAY, 60, busy, HOURS, MSK, LONG_AGO))
    assert times[0] == "11:00"


def test_past_slots_are_hidden() -> None:
    now = msk(14, 10)
    assert local_times(free_slots(DAY, 60, [], HOURS, MSK, now))[0] == "14:30"


def test_slot_starting_exactly_now_is_hidden() -> None:
    now = msk(14, 0)
    assert local_times(free_slots(DAY, 60, [], HOURS, MSK, now))[0] == "14:30"


def test_day_in_the_past_has_no_slots() -> None:
    assert free_slots(DAY, 60, [], HOURS, MSK, msk(9, day=DAY + timedelta(days=1))) == []


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError):
        free_slots(DAY, 60, [], HOURS, MSK, datetime(2026, 11, 10, 9, 0))


def test_slots_are_returned_in_utc() -> None:
    first = free_slots(DAY, 60, [], HOURS, MSK, LONG_AGO)[0]
    assert first.tzinfo == timezone.utc
    assert first == datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc)


def test_dst_switch_keeps_local_working_hours() -> None:
    """Berlin switches to summer time on 2026-03-29: 10:00 local is 09:00 UTC before, 08:00 after."""
    before = free_slots(date(2026, 3, 28), 60, [], HOURS, BERLIN, LONG_AGO)
    after = free_slots(date(2026, 3, 29), 60, [], HOURS, BERLIN, LONG_AGO)
    assert before[0] == datetime(2026, 3, 28, 9, 0, tzinfo=timezone.utc)
    assert after[0] == datetime(2026, 3, 29, 8, 0, tzinfo=timezone.utc)
    assert local_times(after, BERLIN)[0] == "10:00"
    assert local_times(after, BERLIN)[-1] == "19:00"


def test_working_window_in_local_zone() -> None:
    window = working_window(DAY, HOURS, MSK)
    assert window.start.astimezone(timezone.utc).hour == 7
    assert window.end.astimezone(timezone.utc).hour == 17


def test_booking_dates_use_business_time_zone() -> None:
    """22:30 UTC on Nov 1 is already Nov 2 in Moscow."""
    now = datetime(2026, 11, 1, 22, 30, tzinfo=timezone.utc)
    dates = booking_dates(now, MSK, 14)
    assert dates[0] == date(2026, 11, 2)
    assert dates[-1] == date(2026, 11, 15)
    assert len(dates) == 14
