import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.types import LinkPreviewOptions, Message

from database.models import BusinessMessageModel
from utils.logging import setup_logger

logger = setup_logger("business.services.media")


async def _send_photo(
    bot: Bot, chat_id: int, file_id: str, caption: str | None = None
) -> None:
    await bot.send_photo(chat_id, photo=file_id, caption=caption, parse_mode="html")


async def _send_video(
    bot: Bot, chat_id: int, file_id: str, caption: str | None = None
) -> None:
    await bot.send_video(chat_id, video=file_id, caption=caption, parse_mode="html")


async def _send_voice(
    bot: Bot, chat_id: int, file_id: str, caption: str | None = None
) -> None:
    await bot.send_voice(chat_id, voice=file_id, caption=caption, parse_mode="html")


async def _send_audio(
    bot: Bot, chat_id: int, file_id: str, caption: str | None = None
) -> None:
    await bot.send_audio(chat_id, audio=file_id, caption=caption, parse_mode="html")


async def _send_document(
    bot: Bot, chat_id: int, file_id: str, caption: str | None = None
) -> None:
    await bot.send_document(
        chat_id, document=file_id, caption=caption, parse_mode="html"
    )


async def _send_animation(
    bot: Bot, chat_id: int, file_id: str, caption: str | None = None
) -> None:
    await bot.send_animation(
        chat_id, animation=file_id, caption=caption, parse_mode="html"
    )


async def _send_video_note(
    bot: Bot, chat_id: int, file_id: str, caption: str | None = None
) -> None:
    if caption:
        await bot.send_message(chat_id, caption, parse_mode="html")
    await bot.send_video_note(chat_id, video_note=file_id)


async def _send_sticker(
    bot: Bot, chat_id: int, file_id: str, caption: str | None = None
) -> None:
    if caption:
        await bot.send_message(chat_id, caption, parse_mode="html")
    await bot.send_sticker(chat_id, sticker=file_id)


MediaSender = Callable[[Bot, int, str, str | None], Awaitable[None]]

MEDIA_SENDERS: dict[str, MediaSender] = {
    "photo": _send_photo,
    "video": _send_video,
    "voice": _send_voice,
    "audio": _send_audio,
    "document": _send_document,
    "animation": _send_animation,
    "video_note": _send_video_note,
    "sticker": _send_sticker,
}

MEDIA_EXTRACTORS = (
    (
        "photo",
        lambda m: (
            (m.photo[-1].file_id, m.photo[-1].file_unique_id) if m.photo else None
        ),
    ),
    ("video", lambda m: (m.video.file_id, m.video.file_unique_id) if m.video else None),
    (
        "video_note",
        lambda m: (
            (m.video_note.file_id, m.video_note.file_unique_id)
            if m.video_note
            else None
        ),
    ),
    ("voice", lambda m: (m.voice.file_id, m.voice.file_unique_id) if m.voice else None),
    ("audio", lambda m: (m.audio.file_id, m.audio.file_unique_id) if m.audio else None),
    (
        "document",
        lambda m: (
            (m.document.file_id, m.document.file_unique_id) if m.document else None
        ),
    ),
    (
        "sticker",
        lambda m: (m.sticker.file_id, m.sticker.file_unique_id) if m.sticker else None,
    ),
    (
        "animation",
        lambda m: (
            (m.animation.file_id, m.animation.file_unique_id) if m.animation else None
        ),
    ),
)


def extract_media(message: Message) -> tuple[str | None, str | None, str | None]:
    """Extracts media type, file_id, and file_unique_id from a message."""
    for m_type, getter in MEDIA_EXTRACTORS:
        if ids := getter(message):
            return m_type, ids[0], ids[1]
    return None, None, None


async def safe_telegram_call(
    func: Callable[..., Awaitable[Any]],
    *args: Any,
    max_retries: int = 3,
    **kwargs: Any,
) -> Any:
    """Executes a Telegram API call with automatic FloodWait backoff and network retries."""
    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as e:
            logger.warning(
                "Telegram FloodWait encountered, sleeping %s sec", e.retry_after
            )
            await asyncio.sleep(e.retry_after + 1)
        except TelegramNetworkError as e:
            if attempt == max_retries:
                logger.error(
                    "Network error on attempt %d/%d: %s", attempt, max_retries, e
                )
                raise
            logger.warning(
                "Network glitch on attempt %d/%d, retrying: %s",
                attempt,
                max_retries,
                e,
            )
            await asyncio.sleep(0.5 * attempt)
        except TelegramAPIError:
            raise
    return None


async def send_media_attachment(
    bot: Bot,
    chat_id: int,
    media_type: str | None,
    file_id: str | None,
    caption: str | None = None,
) -> None:
    """Sends a media file attachment to a chat with optional caption."""
    if not media_type or not file_id:
        return

    sender = MEDIA_SENDERS.get(media_type)
    if not sender:
        return

    try:
        await safe_telegram_call(sender, bot, chat_id, file_id, caption)
    except TelegramAPIError as e:
        logger.error("Failed to send media attachment (%s): %s", media_type, e)


async def deliver_deleted_media(
    bot: Bot, chat_id: int, msg: BusinessMessageModel, report: str
) -> None:
    """Dispatches deleted message report and media to user without 1024-char caption limits."""
    # 1. Always send the full text report card first (supports up to 4096 chars)
    try:
        await safe_telegram_call(
            bot.send_message,
            chat_id,
            report,
            parse_mode="html",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramAPIError as e:
        logger.error("Failed to send deleted message report: %s", e)
        return

    # 2. If the deleted message had media, send the restored media file below
    if msg.media_type and msg.media_file_id:
        await send_media_attachment(bot, chat_id, msg.media_type, msg.media_file_id)


async def deliver_edited_media(
    bot: Bot,
    chat_id: int,
    cached: BusinessMessageModel,
    new_media_type: str | None,
    new_media_file_id: str | None,
    new_media_file_unique_id: str | None,
) -> None:
    """Dispatches media files for edited business messages (Scenario A or B)."""
    is_same_file = (
        bool(cached.media_file_unique_id)
        and bool(new_media_file_unique_id)
        and cached.media_file_unique_id == new_media_file_unique_id
        and cached.media_type == new_media_type
    )

    if is_same_file:
        await send_media_attachment(
            bot, chat_id, cached.media_type, cached.media_file_id
        )
    else:
        if cached.media_type and cached.media_file_id:
            await send_media_attachment(
                bot,
                chat_id,
                cached.media_type,
                cached.media_file_id,
                caption="🔻 <b>Было:</b> старое вложение",
            )
        if new_media_type and new_media_file_id:
            await send_media_attachment(
                bot,
                chat_id,
                new_media_type,
                new_media_file_id,
                caption="🔺 <b>Стало:</b> новое вложение",
            )
