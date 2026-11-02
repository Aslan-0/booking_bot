"""Google Sheets export via gspread.

gspread is synchronous, so every call runs in a worker thread through
`asyncio.to_thread`. Any Sheets failure is logged and swallowed: the DB is
the source of truth, the spreadsheet is a convenience mirror.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
import requests
from google.auth.exceptions import GoogleAuthError

from db.models import BookingStatus
from services.booking_service import BookingInfo

logger = logging.getLogger(__name__)

HEADER: tuple[str, ...] = (
    "ID", "Created", "Client", "Telegram ID", "Service",
    "Date", "Time", "Price", "Status",
)
ID_COLUMN = 1
STATUS_COLUMN = HEADER.index("Status") + 1
NEW_SHEET_ROWS = 1000

STATUS_LABELS: dict[BookingStatus, str] = {
    BookingStatus.ACTIVE: "Active",
    BookingStatus.CANCELLED: "Cancelled",
    BookingStatus.DONE: "Completed",
}

SHEETS_ERRORS: tuple[type[Exception], ...] = (
    gspread.exceptions.GSpreadException,
    GoogleAuthError,
    requests.RequestException,
    OSError,
    ValueError,
)


class SheetsClient:
    """Append bookings and update their status in a Google Sheet."""

    def __init__(
        self, credentials_file: Path, spreadsheet_id: str, worksheet_title: str, tz: ZoneInfo
    ) -> None:
        self._credentials_file = credentials_file
        self._spreadsheet_id = spreadsheet_id
        self._worksheet_title = worksheet_title
        self._tz = tz
        self._worksheet: gspread.Worksheet | None = None
        self._lock = asyncio.Lock()

    async def append_booking(self, info: BookingInfo) -> bool:
        """Add a row for a new booking. Return False on failure (already logged)."""
        return await self._run("append booking", info.id, self._append_sync, info)

    async def update_status(self, booking_id: int, status: BookingStatus) -> bool:
        """Update the status cell of a booking row. Return False on failure."""
        return await self._run("update status", booking_id, self._update_status_sync, booking_id, status)

    async def check_connection(self) -> bool:
        """Open the worksheet once at startup to surface config errors early."""
        return await self._run("connect", 0, self._get_worksheet)

    async def _run(
        self, action: str, booking_id: int, func: Callable[..., object], *args: object
    ) -> bool:
        async with self._lock:
            try:
                await asyncio.to_thread(func, *args)
            except SHEETS_ERRORS as exc:
                self._worksheet = None
                logger.error(
                    "Google Sheets: failed to %s (booking %s): %s: %s",
                    action, booking_id, type(exc).__name__, exc,
                )
                return False
        return True

    def _get_worksheet(self) -> gspread.Worksheet:
        """Open (or create) the worksheet and ensure the header row exists."""
        if self._worksheet is not None:
            return self._worksheet
        client = gspread.service_account(filename=self._credentials_file)
        spreadsheet = client.open_by_key(self._spreadsheet_id)
        try:
            worksheet = spreadsheet.worksheet(self._worksheet_title)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                self._worksheet_title, rows=NEW_SHEET_ROWS, cols=len(HEADER)
            )
            logger.info("Google Sheets: created worksheet %r", self._worksheet_title)
        if worksheet.row_values(1) != list(HEADER):
            worksheet.update([list(HEADER)], "A1")
        self._worksheet = worksheet
        return worksheet

    def _local(self, dt: datetime, fmt: str) -> str:
        return dt.astimezone(self._tz).strftime(fmt)

    def _append_sync(self, info: BookingInfo) -> None:
        row: list[str | int] = [
            info.id,
            self._local(info.created_at, "%d.%m.%Y %H:%M"),
            info.client_name,
            info.client_telegram_id,
            info.service_title,
            self._local(info.start_at, "%d.%m.%Y"),
            f"{self._local(info.start_at, '%H:%M')}–{self._local(info.end_at, '%H:%M')}",
            info.price,
            STATUS_LABELS[info.status],
        ]
        self._get_worksheet().append_row(row, value_input_option=gspread.utils.ValueInputOption.raw)
        logger.info("Google Sheets: booking %s appended", info.id)

    def _update_status_sync(self, booking_id: int, status: BookingStatus) -> None:
        worksheet = self._get_worksheet()
        cell = worksheet.find(str(booking_id), in_column=ID_COLUMN)
        if cell is None:
            logger.warning("Google Sheets: booking %s not found in the sheet", booking_id)
            return
        worksheet.update_cell(cell.row, STATUS_COLUMN, STATUS_LABELS[status])
        logger.info("Google Sheets: booking %s status -> %s", booking_id, status.value)
