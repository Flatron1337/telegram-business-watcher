import time
from typing import Any

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    LinkPreviewOptions,
    Message,
)

from config.settings import Settings
from database.models import BusinessConnectionModel, BusinessMessageModel
from database.repository import BusinessRepository
from services.formatter import BusinessReportFormatter
from utils.logging import setup_logger

logger = setup_logger("business.handlers")
router = Router(name="business")


def _to_timestamp(val: Any) -> int:
    """Converts datetime, integer, or float timestamp to integer unix timestamp."""
    if val is None:
        return int(time.time())
    if hasattr(val, "timestamp"):
        return int(val.timestamp())
    if isinstance(val, (int, float)):
        return int(val)
    return int(time.time())


def _extract_media(message: Message) -> tuple[str | None, str | None]:
    """Extracts media type and telegram file_id from a message."""
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.video:
        return "video", message.video.file_id
    if message.video_note:
        return "video_note", message.video_note.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.document:
        return "document", message.document.file_id
    if message.sticker:
        return "sticker", message.sticker.file_id
    if message.animation:
        return "animation", message.animation.file_id
    return None, None


@router.business_connection()
async def on_business_connection(
    connection: BusinessConnection,
    bot: Bot,
    repository: BusinessRepository,
    settings: Settings,
):
    """Fired when a user connects or disconnects the bot in Chat Automation settings."""
    if not settings.is_user_allowed(connection.user.id):
        logger.warning(
            "Rejected unauthorized business connection: user_id=%s, id=%s",
            connection.user.id,
            connection.id,
        )
        try:
            await bot.send_message(
                connection.user_chat_id,
                "⛔️ <b>Доступ ограничен</b>\n\n"
                "Этот бот является персональным и доступен только владельцу.",
                parse_mode="html",
            )
        except TelegramAPIError as e:
            logger.warning(
                "Could not send rejection message to user %s: %s",
                connection.user_chat_id,
                e,
            )
        return
    conn_model = BusinessConnectionModel(
        connection_id=connection.id,
        user_id=connection.user.id,
        user_chat_id=connection.user_chat_id,
        can_reply=bool(connection.can_reply),
        is_enabled=bool(connection.is_enabled),
        connected_at=_to_timestamp(connection.date),
    )
    await repository.save_connection(conn_model)

    status_str = "подключен" if connection.is_enabled else "отключен"
    logger.info(
        "Business connection updated: user_id=%s, id=%s, status=%s",
        connection.user.id,
        connection.id,
        status_str,
    )

    if connection.is_enabled:
        text = (
            "🎉 <b>Бот успешно привязан к вашему аккаунту!</b>\n\n"
            "С этого момента я фиксирую все входящие и исходящие сообщения в ваших личных чатах.\n"
            "Если собеседник удалит или отредактирует сообщение, голосовое или кружок — "
            "вы моментально получите отчёт прямо сюда.\n\n"
            f"📌 <i>Служебная привязка:</i> <code>BUSINESS_CONN:{connection.id}</code>"
        )
        try:
            sent_msg = await bot.send_message(
                connection.user_chat_id, text, parse_mode="html"
            )
            await bot.pin_chat_message(
                chat_id=connection.user_chat_id,
                message_id=sent_msg.message_id,
                disable_notification=True,
            )
        except TelegramAPIError as e:
            logger.warning(
                "Could not send/pin confirmation to user %s: %s",
                connection.user_chat_id,
                e,
            )


async def ensure_business_connection(
    bot: Bot,
    repository: BusinessRepository,
    settings: Settings,
    connection_id: str,
) -> BusinessConnectionModel | None:
    """Retrieves business connection from repository, auto-restoring from Telegram API if absent."""
    conn = await repository.get_connection_by_id(connection_id)
    if conn and conn.is_enabled:
        return conn

    try:
        tg_conn = await bot.get_business_connection(connection_id)
        if tg_conn and tg_conn.is_enabled and settings.is_user_allowed(tg_conn.user.id):
            conn = BusinessConnectionModel(
                connection_id=tg_conn.id,
                user_id=tg_conn.user.id,
                user_chat_id=tg_conn.user_chat_id,
                can_reply=bool(tg_conn.can_reply),
                is_enabled=bool(tg_conn.is_enabled),
                connected_at=_to_timestamp(tg_conn.date),
            )
            await repository.save_connection(conn)
            logger.info(
                "Auto-restored business connection from Telegram API: user_id=%s, id=%s",
                tg_conn.user.id,
                tg_conn.id,
            )
            return conn
    except TelegramAPIError as e:
        logger.warning(
            "Could not auto-restore business connection %s: %s", connection_id, e
        )
    return None


@router.business_message()
async def on_business_message(
    message: Message, bot: Bot, repository: BusinessRepository, settings: Settings
):
    """Caches incoming and outgoing messages from business chats."""
    if not message.business_connection_id:
        return

    conn = await ensure_business_connection(
        bot, repository, settings, message.business_connection_id
    )
    if not conn or not conn.is_enabled or not settings.is_user_allowed(conn.user_id):
        return

    media_type, media_file_id = _extract_media(message)
    content_text = message.text or message.caption or ""

    sender = message.from_user
    if sender:
        first = sender.first_name or ""
        last = sender.last_name or ""
        sender_name = f"{first} {last}".strip() or "User"
        sender_username = sender.username
        sender_id = sender.id
    else:
        sender_name = message.chat.full_name or "Chat"
        sender_username = message.chat.username
        sender_id = message.chat.id

    msg_model = BusinessMessageModel(
        connection_id=message.business_connection_id,
        chat_id=message.chat.id,
        chat_title=message.chat.full_name or message.chat.title,
        chat_username=message.chat.username,
        message_id=message.message_id,
        sender_id=sender_id,
        sender_name=sender_name,
        sender_username=sender_username,
        date=_to_timestamp(message.date),
        text=content_text,
        media_type=media_type,
        media_file_id=media_file_id,
    )

    await repository.save_message(msg_model)
    logger.debug(
        "Cached business message: conn=%s, chat=%s, msg=%s",
        message.business_connection_id,
        message.chat.id,
        message.message_id,
    )


@router.edited_business_message()
async def on_edited_business_message(
    message: Message, bot: Bot, repository: BusinessRepository, settings: Settings
):
    """Fired when an existing business message is edited."""
    if not message.business_connection_id:
        return

    conn = await ensure_business_connection(
        bot, repository, settings, message.business_connection_id
    )
    if not conn or not conn.is_enabled or not settings.is_user_allowed(conn.user_id):
        return

    new_text = message.text or message.caption or ""
    cached = await repository.get_message(
        message.business_connection_id, message.chat.id, message.message_id
    )

    if not cached or (cached.text or "") == new_text:
        return

    edited_at = _to_timestamp(message.edit_date or message.date)
    report = BusinessReportFormatter.format_edited_report(
        msg=cached,
        new_text=new_text,
        edited_at=edited_at,
    )

    try:
        await bot.send_message(
            conn.user_chat_id,
            report,
            parse_mode="html",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
        logger.info(
            "Reported edited message to user=%s (chat=%s, msg=%s)",
            conn.user_id,
            message.chat.id,
            message.message_id,
        )
    except TelegramAPIError as e:
        logger.error("Failed to send edit report to user: %s", e)

    await repository.record_edit(
        connection_id=message.business_connection_id,
        chat_id=message.chat.id,
        message_id=message.message_id,
        old_text=cached.text or "",
        new_text=new_text,
        edited_at=edited_at,
    )


MEDIA_DIRECT_SENDERS = {
    "photo": lambda bot, chat_id, file_id, caption: bot.send_photo(
        chat_id, photo=file_id, caption=caption, parse_mode="html"
    ),
    "video": lambda bot, chat_id, file_id, caption: bot.send_video(
        chat_id, video=file_id, caption=caption, parse_mode="html"
    ),
    "voice": lambda bot, chat_id, file_id, caption: bot.send_voice(
        chat_id, voice=file_id, caption=caption, parse_mode="html"
    ),
    "audio": lambda bot, chat_id, file_id, caption: bot.send_audio(
        chat_id, audio=file_id, caption=caption, parse_mode="html"
    ),
    "document": lambda bot, chat_id, file_id, caption: bot.send_document(
        chat_id, document=file_id, caption=caption, parse_mode="html"
    ),
    "animation": lambda bot, chat_id, file_id, caption: bot.send_animation(
        chat_id, animation=file_id, caption=caption, parse_mode="html"
    ),
}


async def _deliver_deleted_media(
    bot: Bot, chat_id: int, msg: BusinessMessageModel, report: str
) -> None:
    """Dispatches appropriate media message to user using a dispatch map."""
    file_id = msg.media_file_id
    assert file_id is not None
    m_type = msg.media_type or ""

    if sender_fn := MEDIA_DIRECT_SENDERS.get(m_type):
        await sender_fn(bot, chat_id, file_id, report)
        return

    if m_type == "video_note":
        await bot.send_message(chat_id, report, parse_mode="html")
        await bot.send_video_note(chat_id, video_note=file_id)
        return

    if m_type == "sticker":
        await bot.send_message(chat_id, report, parse_mode="html")
        await bot.send_sticker(chat_id, sticker=file_id)
        return

    await bot.send_message(
        chat_id,
        report,
        parse_mode="html",
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )


@router.deleted_business_messages()
async def on_deleted_business_messages(
    event: BusinessMessagesDeleted,
    bot: Bot,
    repository: BusinessRepository,
    settings: Settings,
):
    """Fired when messages in a business chat are deleted."""
    conn = await ensure_business_connection(
        bot, repository, settings, event.business_connection_id
    )
    if not conn or not conn.is_enabled or not settings.is_user_allowed(conn.user_id):
        return

    now_ts = int(time.time())

    for msg_id in event.message_ids:
        msg = await repository.mark_deleted(
            event.business_connection_id, event.chat.id, msg_id, now_ts
        )
        if not msg:
            logger.debug("Deleted message msg_id=%s not found in local store", msg_id)
            continue

        has_media = bool(msg.media_file_id)
        report = BusinessReportFormatter.format_deleted_report(msg, has_media=has_media)

        try:
            if has_media:
                await _deliver_deleted_media(bot, conn.user_chat_id, msg, report)
            else:
                await bot.send_message(
                    conn.user_chat_id,
                    report,
                    parse_mode="html",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )

            logger.info(
                "Reported deleted message to user=%s (chat=%s, msg=%s)",
                conn.user_id,
                event.chat.id,
                msg_id,
            )
        except TelegramAPIError as e:
            logger.error("Failed to send deleted message report to user: %s", e)
