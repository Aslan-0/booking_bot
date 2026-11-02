"""ORM models. All datetimes are stored in UTC."""

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Return current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator[datetime]):
    """Store datetimes as naive UTC, return them as aware UTC.

    SQLite has no native time zone support, so we normalise on write
    and re-attach tzinfo on read.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetime is not allowed, pass an aware datetime")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all models."""


class BookingStatus(str, enum.Enum):
    """Lifecycle of a booking."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    DONE = "done"


class User(Base):
    """Telegram user (client)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    bookings: Mapped[list["Booking"]] = relationship(back_populates="user")


class Service(Base):
    """A service that can be booked (haircut, manicure, ...)."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    duration_min: Mapped[int] = mapped_column(Integer)
    price: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    bookings: Mapped[list["Booking"]] = relationship(back_populates="service")


class Booking(Base):
    """A client's booking for a service in a time interval [start_at, end_at)."""

    __tablename__ = "bookings"
    __table_args__ = (Index("ix_bookings_status_start", "status", "start_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    start_at: Mapped[datetime] = mapped_column(UTCDateTime)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[BookingStatus] = mapped_column(
        Enum(BookingStatus, native_enum=False, length=16,
             values_callable=lambda e: [m.value for m in e]),
        default=BookingStatus.ACTIVE,
    )
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="bookings")
    service: Mapped[Service] = relationship(back_populates="bookings")
