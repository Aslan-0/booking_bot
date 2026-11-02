"""Filter that passes only users listed in ADMIN_IDS."""

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from config import settings


class AdminFilter(BaseFilter):
    """True if the event comes from an admin."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return user is not None and user.id in settings.admin_ids
