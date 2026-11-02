"""/start command, main menu navigation and fallback for stale buttons.

This router must be included LAST: its fallback catches any callback
that no other handler matched (e.g. a button from an old message).
"""

import html
import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from db.repo import get_or_create_user
from keyboards.callbacks import MenuAction, MenuCallback
from keyboards.inline import main_menu_kb

logger = logging.getLogger(__name__)
router = Router(name="start")

WELCOME_TEXT = (
    "Hello, <b>{name}</b>!\n\n"
    "I can help you book a service. Choose an action:"
)


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    """Register the user and show the main menu."""
    if message.from_user is None:
        return
    await state.clear()
    user = await get_or_create_user(
        session,
        telegram_id=message.from_user.id,
        name=message.from_user.full_name,
    )
    await message.answer(
        WELCOME_TEXT.format(name=html.escape(user.name)), reply_markup=main_menu_kb()
    )


@router.callback_query(MenuCallback.filter(F.action == MenuAction.HOME))
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """Return to the main menu from any screen."""
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            WELCOME_TEXT.format(name=html.escape(callback.from_user.full_name)),
            reply_markup=main_menu_kb(),
        )
    await callback.answer()


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    """Answer buttons that no longer match the current state."""
    await callback.answer("This button is outdated. Press /start", show_alert=True)
