"""Application settings loaded from environment variables / .env file."""

from datetime import time
from functools import cached_property
from pathlib import Path
from typing import Annotated, Self
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Bot configuration. All secrets come only from .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: SecretStr = SecretStr("")
    admin_ids: Annotated[list[int], NoDecode] = []

    database_url: str = "sqlite+aiosqlite:///booking.db"

    timezone: str = "Europe/Moscow"
    work_start: time = time(10, 0)
    work_end: time = time(20, 0)
    slot_step_min: int = 30
    booking_days_ahead: int = 14
    reminder_before_min: int = 60

    google_credentials_file: Path | None = None
    google_spreadsheet_id: str | None = None
    google_worksheet: str = "Bookings"

    throttle_rate_sec: float = 0.5
    log_level: str = "INFO"

    @field_validator("bot_token")
    @classmethod
    def _require_token(cls, value: SecretStr) -> SecretStr:
        """Fail fast with a clear message when BOT_TOKEN is missing."""
        if not value.get_secret_value().strip():
            raise ValueError("BOT_TOKEN is not set, add it to .env")
        return value

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        """Accept a comma-separated string like '123,456'."""
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("google_credentials_file", "google_spreadsheet_id", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        """Treat an empty env value as 'not configured'."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        """Fail fast on an unknown time zone name."""
        ZoneInfo(value)
        return value

    @model_validator(mode="after")
    def _check_schedule(self) -> Self:
        """Validate working hours and booking horizon."""
        if self.work_start >= self.work_end:
            raise ValueError("WORK_START must be earlier than WORK_END")
        if self.slot_step_min <= 0 or self.booking_days_ahead <= 0:
            raise ValueError("SLOT_STEP_MIN and BOOKING_DAYS_AHEAD must be positive")
        if self.reminder_before_min <= 0:
            raise ValueError("REMINDER_BEFORE_MIN must be positive")
        return self

    @cached_property
    def tz(self) -> ZoneInfo:
        """Business time zone as a ZoneInfo object."""
        return ZoneInfo(self.timezone)

    @property
    def sheets_enabled(self) -> bool:
        """Google Sheets export is on when both credentials and sheet id are set."""
        return self.google_credentials_file is not None and bool(self.google_spreadsheet_id)


settings = Settings()
