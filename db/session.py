"""Async engine, session factory and schema initialisation."""

import logging
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from db.models import Base

logger = logging.getLogger(__name__)

SQLITE_BEGIN_MODE = "sqlite_begin_mode"
_ALLOWED_BEGIN_MODES = frozenset({"DEFERRED", "IMMEDIATE", "EXCLUSIVE"})


def _setup_sqlite(engine: AsyncEngine) -> None:
    """Take control of BEGIN so transactions can be started as IMMEDIATE.

    Recipe from SQLAlchemy docs ("Serializable isolation / Savepoints /
    Transactional DDL", aiosqlite): disable the driver's own transaction
    handling and emit BEGIN ourselves.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _on_begin(conn: Connection) -> None:
        mode = conn.get_execution_options().get(SQLITE_BEGIN_MODE, "DEFERRED")
        if mode not in _ALLOWED_BEGIN_MODES:
            raise ValueError(f"Unsupported SQLite BEGIN mode: {mode}")
        conn.exec_driver_sql(f"BEGIN {mode}")


def create_engine(database_url: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine (with SQLite transaction control)."""
    engine = create_async_engine(database_url, echo=False)
    if engine.dialect.name == "sqlite":
        _setup_sqlite(engine)
    return engine


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a session factory; objects stay usable after commit."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create all tables if they do not exist yet."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema is ready")
