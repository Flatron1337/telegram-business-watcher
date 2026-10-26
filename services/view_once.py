import io
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, LinkPreviewOptions, Message

from database.models import BusinessConnectionModel, BusinessMessageModel
from database.repository import REDIS_EXCEPTIONS, BusinessRepository
from services.formatter import BusinessReportFormatter
from services.media import extract_media, safe_telegram_call
from utils.logging import setup_logger

logger = setup_logger("business.services.view_once")

_INTERCEPTED_MESSAGES: set[str] = set()

MEDIA_EXTENSIONS: dict[str, str] = {
    "photo": ".jpg",
    "video": ".mp4",
    "video_note": ".mp4",
    "voice": ".ogg",
    "audio": ".mp3",
    "document": ".bin",
    "animation": ".mp4",
    "sticker": ".webp",
}


async def _send_buffered_photo(bot: Bot, chat_id: int, file: BufferedInputFile) -> None:
    await bot.send_photo(chat_id, photo=file)


async def _send_buffered_video(bot: Bot, chat_id: int, file: BufferedInputFile) -> None:
    await bot.send_video(chat_id, video=file)


async def _send_buffered_voice(bot: Bot, chat_id: int, file: BufferedInputFile) -> None:
    await bot.send_voice(chat_id, voice=file)


async def _send_buffered_video_note(
    bot: Bot, chat_id: int, file: BufferedInputFile
) -> None:
    await bot.send_video_note(chat_id, video_note=file)


async def _send_buffered_audio(bot: Bot, chat_id: int, file: BufferedInputFile) -> None:
    await bot.send_audio(chat_id, audio=file)


async def _send_buffered_document(
    bot: Bot, chat_id: int, file: BufferedInputFile
) -> None:
    await bot.send_document(chat_id, document=file)


async def _send_buffered_animation(
    bot: Bot, chat_id: int, file: BufferedInputFile
) -> None:
    await bot.send_animation(chat_id, animation=file)


async def _send_buffered_sticker(
    bot: Bot, chat_id: int, file: BufferedInputFile
) -> None:
    await bot.send_sticker(chat_id, sticker=file)


BUFFERED_SENDERS: dict[
    str, Callable[[Bot, int, BufferedInputFile], Awaitable[None]]
] = {
    "photo": _send_buffered_photo,
    "video": _send_buffered_video,
    "voice": _send_buffered_voice,
    "video_note": _send_buffered_video_note,
    "audio": _send_buffered_audio,
    "document": _send_buffered_document,
    "animation": _send_buffered_animation,
    "sticker": _send_buffered_sticker,
}


def _to_timestamp(dt: Any) -> int:
    if isinstance(dt, datetime):
        return int(dt.timestamp())
    if isinstance(dt, (int, float)):
        return int(dt)
    return int(time.time())


async def deliver_view_once_media(
    bot: Bot,
    chat_id: int,
    msg: BusinessMessageModel,
) -> bool:
    """Downloads view-once media into RAM and re-uploads it permanently to user."""
    if not msg.media_type or not msg.media_file_id:
        return False

    buffer = io.BytesIO()
    try:
        await bot.download(msg.media_file_id, destination=buffer)
    except (TelegramAPIError, OSError) as e:
        logger.error("Failed to download view-once media %s: %s", msg.media_file_id, e)
        return False

    content_bytes = buffer.getvalue()
    if not content_bytes:
        logger.warning("Downloaded 0 bytes for view-once media %s", msg.media_file_id)
        return False

    ext = MEDIA_EXTENSIONS.get(msg.media_type, ".bin")
    filename = f"view_once_{msg.chat_id}_{msg.message_id}{ext}"
    input_file = BufferedInputFile(content_bytes, filename=filename)

    report = BusinessReportFormatter.format_view_once_report(msg)

    try:
        await safe_telegram_call(
            bot.send_message,
            chat_id,
            report,
            parse_mode="html",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramAPIError as e:
        logger.error("Failed to send view-once report card: %s", e)

    sender = BUFFERED_SENDERS.get(msg.media_type)
    if sender:
        try:
            await safe_telegram_call(sender, bot, chat_id, input_file)
        except TelegramAPIError as e:
            logger.error("Failed to send re-uploaded view-once file: %s", e)

    return True


def _extract_reply_sender(reply: Message) -> tuple[int, str, str | None]:
    sender = reply.from_user
    if sender:
        first = sender.first_name or ""
        last = sender.last_name or ""
        sender_name = f"{first} {last}".strip() or "User"
        return sender.id, sender_name, sender.username
    sender_name = reply.chat.full_name or "Chat"
    return reply.chat.id, sender_name, reply.chat.username


async def handle_view_once_reply(
    message: Message,
    conn: BusinessConnectionModel,
    bot: Bot,
    repository: BusinessRepository,
) -> bool:
    """Detects and intercepts view-once/disappearing media via reply_to_message."""
    reply = message.reply_to_message
    if not reply:
        return False

    media_type, media_file_id, media_file_unique_id = extract_media(reply)
    if not media_type or not media_file_id:
        return False

    cache_key = f"{conn.connection_id}:{reply.chat.id}:{reply.message_id}"
    if cache_key in _INTERCEPTED_MESSAGES:
        return False

    if repository.redis:
        try:
            if await repository.redis.sismember("intercepted_view_once", cache_key):
                return False
        except REDIS_EXCEPTIONS as e:
            logger.debug("Redis check error for view_once: %s", e)

    existing = await repository.get_message(
        conn.connection_id, reply.chat.id, reply.message_id
    )

    is_protected = getattr(reply, "has_protected_content", False) is True
    if not (existing is None or is_protected):
        return False

    sender_id, sender_name, sender_username = _extract_reply_sender(reply)
    content_text = reply.text or reply.caption or ""
    date_ts = _to_timestamp(getattr(reply, "date", None))

    reply_model = BusinessMessageModel(
        connection_id=conn.connection_id,
        chat_id=reply.chat.id,
        chat_title=reply.chat.full_name or reply.chat.title,
        chat_username=reply.chat.username,
        message_id=reply.message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        sender_username=sender_username,
        date=date_ts,
        text=content_text,
        media_type=media_type,
        media_file_id=media_file_id,
        media_file_unique_id=media_file_unique_id,
    )

    delivered = await deliver_view_once_media(bot, conn.user_chat_id, reply_model)
    if delivered:
        _INTERCEPTED_MESSAGES.add(cache_key)
        if repository.redis:
            try:
                await repository.redis.sadd("intercepted_view_once", cache_key)
            except REDIS_EXCEPTIONS as e:
                logger.debug("Redis sadd error for view_once: %s", e)

        await repository.save_message(reply_model)
        logger.info(
            "Intercepted view-once media: user=%s, chat=%s, msg=%s",
            conn.user_id,
            reply.chat.id,
            reply.message_id,
        )
        return True
    return False
