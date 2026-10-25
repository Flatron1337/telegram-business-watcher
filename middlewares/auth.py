from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config.settings import Settings
from utils.logging import setup_logger

logger = setup_logger("business.middleware.auth")


class AuthMiddleware(BaseMiddleware):
    """Restricts bot access to authorized Telegram user IDs."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        settings: Settings | None = data.get("settings")
        if not settings or not settings.allowed_user_ids:
            return await handler(event, data)

        user = getattr(event, "from_user", None)
        if user and not settings.is_user_allowed(user.id):
            logger.warning(
                "Access blocked for unauthorized user_id=%d (@%s)",
                user.id,
                user.username,
            )
            if isinstance(event, Message):
                await event.answer(
                    "⛔️ <b>Доступ ограничен</b>\n\n"
                    "Этот бот является персональным и доступен только владельцу.",
                    parse_mode="html",
                )
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔️ Доступ ограничен.", show_alert=True)
            return None

        return await handler(event, data)
