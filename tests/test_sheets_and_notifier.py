"""Sheets client degrades gracefully; notifier escapes user input."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import gspread

from db.models import BookingStatus
from services.booking_service import BookingInfo
from services.notifier import Notifier
from services.sheets import HEADER, STATUS_COLUMN, SheetsClient

MSK = ZoneInfo("Europe/Moscow")
INFO = BookingInfo(
    id=42,
    service_title="Men's haircut",
    price=1500,
    start_at=datetime(2026, 11, 10, 7, 0, tzinfo=timezone.utc),
    end_at=datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc),
    client_name="<b>=HACK()</b>",
    client_telegram_id=777,
    status=BookingStatus.ACTIVE,
    created_at=datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc),
)


class FakeWorksheet:
    """Minimal in-memory stand-in for gspread.Worksheet."""

    def __init__(self) -> None:
        self.rows: list[list[Any]] = [list(HEADER)]

    def append_row(self, row: list[Any], value_input_option: Any = None) -> None:
        self.rows.append(row)

    def find(self, query: str, in_column: int) -> Any:
        for index, row in enumerate(self.rows, start=1):
            if str(row[in_column - 1]) == query:
                return type("Cell", (), {"row": index})()
        return None

    def update_cell(self, row: int, col: int, value: str) -> None:
        self.rows[row - 1][col - 1] = value


def test_sheets_failure_is_swallowed(tmp_path: Path) -> None:
    client = SheetsClient(tmp_path / "missing.json", "sheet-id", "Bookings", MSK)
    assert asyncio.run(client.append_booking(INFO)) is False
    assert asyncio.run(client.update_status(INFO.id, BookingStatus.CANCELLED)) is False


def test_sheets_api_error_is_swallowed(tmp_path: Path) -> None:
    client = SheetsClient(tmp_path / "x.json", "sheet-id", "Bookings", MSK)

    def broken() -> FakeWorksheet:
        raise gspread.exceptions.GSpreadException("quota exceeded")

    client._get_worksheet = broken
    assert asyncio.run(client.append_booking(INFO)) is False


def test_sheets_row_and_status_update(tmp_path: Path) -> None:
    client = SheetsClient(tmp_path / "x.json", "sheet-id", "Bookings", MSK)
    sheet = FakeWorksheet()
    client._worksheet = sheet

    assert asyncio.run(client.append_booking(INFO)) is True
    row = sheet.rows[1]
    assert row[0] == 42
    assert row[2] == "<b>=HACK()</b>"
    assert row[5:7] == ["10.11.2026", "10:00–11:00"]
    assert row[STATUS_COLUMN - 1] == "Active"

    assert asyncio.run(client.update_status(42, BookingStatus.CANCELLED)) is True
    assert sheet.rows[1][STATUS_COLUMN - 1] == "Cancelled"
    assert asyncio.run(client.update_status(999, BookingStatus.DONE)) is True


def test_notifier_escapes_html_and_reaches_all_admins() -> None:
    sent: list[tuple[int, str]] = []

    async def send(chat_id: int, text: str) -> bool:
        sent.append((chat_id, text))
        return chat_id != 2

    notifier = Notifier(send, [1, 2], MSK)
    asyncio.run(notifier.booking_created(INFO))
    assert [chat for chat, _ in sent] == [1, 2]
    assert "&lt;b&gt;=HACK()&lt;/b&gt;" in sent[0][1]
    assert "<b>=HACK()</b>" not in sent[0][1]
    assert "10:00–11:00" in sent[0][1]
