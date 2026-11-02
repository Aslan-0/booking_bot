"""Entry point: wire all components together and start polling."""

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from config import settings
from db.repo import seed_services
from db.session import create_engine, create_sessionmaker, init_db
from handlers import setup_routers
from middlewares.db import DbSessionMiddleware
from middlewares.throttle import ThrottleMiddleware
from scheduler import ReminderScheduler
from services.booking_events import BookingEvents
from services.notifier import Notifier
from services.sheets import SheetsClient
from utils.telegram_sender import TelegramSender

logger = logging.getLogger(__name__)

COMPLETE_FINISHED_EVERY_MIN = 15


def build_sheets_client() -> SheetsClient | None:
    """Create a Sheets client if Google credentials are configured."""
    credentials, spreadsheet_id = settings.google_credentials_file, settings.google_spreadsheet_id
    if credentials is None or not spreadsheet_id:
        logger.info("Google Sheets export is disabled (credentials not configured)")
        return None
    return SheetsClient(credentials, spreadsheet_id, settings.google_worksheet, settings.tz)


async def main() -> None:
    """Initialise all components and run the bot in polling mode."""
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    engine = create_engine(settings.database_url)
    sessionmaker = create_sessionmaker(engine)
    await init_db(engine)
    async with sessionmaker() as session:
        await seed_services(session)

    bot = Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    notifier = Notifier(TelegramSender(bot), settings.admin_ids, settings.tz)
    sheets = build_sheets_client()
    if sheets is not None and await sheets.check_connection():
        logger.info("Google Sheets connected")
    reminders = ReminderScheduler(
        sessionmaker, notifier, timedelta(minutes=settings.reminder_before_min)
    )
    events = BookingEvents(sessionmaker, notifier, reminders, sheets)

    dp = Dispatcher(events=events)
    dp.message.outer_middleware(ThrottleMiddleware(settings.throttle_rate_sec))
    dp.callback_query.outer_middleware(ThrottleMiddleware(settings.throttle_rate_sec))
    dp.update.middleware(DbSessionMiddleware(sessionmaker))
    dp.include_router(setup_routers())

    reminders.start()
    await reminders.restore()
    reminders.add_periodic(
        events.complete_finished, COMPLETE_FINISHED_EVERY_MIN, job_id="complete-finished"
    )

    me = await bot.get_me()
    await bot.set_my_commands([BotCommand(command="start", description="Main menu")])
    logger.info(
        "Bot @%s started (tz=%s, admins=%d)",
        me.username, settings.timezone, len(settings.admin_ids),
    )
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        reminders.shutdown()
        await events.drain()
        await bot.session.close()
        await engine.dispose()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
