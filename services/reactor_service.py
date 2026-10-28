from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, ReactionTypeEmoji

from config.settings import Settings
from database.models import ReactorConfig
from database.repository import BusinessRepository
from services.media import safe_telegram_call
from services.reactor_mtproto import get_mtproto_client
from utils.logging import setup_logger

logger = setup_logger("business.services.reactor")


def should_react_to_message(
    config: ReactorConfig,
    conn_user_id: int,
    owner_id: int,
    chat_id: int,
    sender_id: int | None,
) -> bool:
    """Determines whether the stealth auto-reactor should react to a business message."""
    if not config.enabled or conn_user_id != owner_id:
        return False
    if config.target_chat_ids and chat_id not in config.target_chat_ids:
        return False
    return not (config.only_incoming and sender_id == owner_id)


async def apply_auto_reaction(
    bot: Bot,
    message: Message,
    repository: BusinessRepository,
    settings: Settings,
    conn_user_id: int,
    mtproto_client: Any | None = None,
) -> bool:
    """Applies the configured emoji reaction via MTProto bridge or Bot API fallback."""
    if conn_user_id != settings.reactor_owner_id:
        return False

    config = await repository.get_reactor_config(settings.reactor_owner_id)
    sender = getattr(message, "from_user", None)
    sender_id = sender.id if sender else None

    if not should_react_to_message(
        config=config,
        conn_user_id=conn_user_id,
        owner_id=settings.reactor_owner_id,
        chat_id=message.chat.id,
        sender_id=sender_id,
    ):
        return False

    active_mtproto = mtproto_client or get_mtproto_client(settings)
    if active_mtproto is not None:
        ok = await active_mtproto.send_reaction(
            chat_id=message.chat.id,
            message_id=message.message_id,
            emoji=config.emoji,
            is_big=config.is_big,
        )
        if ok:
            logger.debug(
                "Applied MTProto stealth reaction (%s) to chat=%s, msg=%s",
                config.emoji,
                message.chat.id,
                message.message_id,
            )
        return bool(ok)

    try:
        await safe_telegram_call(
            bot.set_message_reaction,
            chat_id=message.chat.id,
            message_id=message.message_id,
            reaction=[ReactionTypeEmoji(emoji=config.emoji)],
            is_big=config.is_big,
        )
        logger.debug(
            "Applied Bot API stealth reaction (%s) to chat=%s, msg=%s",
            config.emoji,
            message.chat.id,
            message.message_id,
        )
        return True
    except (TelegramAPIError, OSError, AttributeError) as e:
        logger.warning(
            "Could not set auto-reaction in chat=%s, msg=%s: %s",
            message.chat.id,
            message.message_id,
            e,
        )
        return False
